#!/usr/bin/env python3
"""
Тото резултати - fetcher за GitHub Actions
Дърпа от info.toto.bg и записва JSON файлове в data/

Форматът на data/*.json е същият като преди. Добавени са само нови полета
(draw, draw_date, source_url) и подробна диагностика в data/summary.json.

Exit codes:
  0 - всички игри са обновени
  1 - част от игрите са неуспешни (данните им остават от последния успешен рън)
  2 - всички игри са неуспешни
"""

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone, date

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

BASE_URL = os.environ.get('TOTO_BASE_URL', 'https://info.toto.bg/results/')
HOME_URL = os.environ.get('TOTO_HOME_URL', 'https://info.toto.bg/')
DATA_DIR = os.environ.get('TOTO_DATA_DIR', 'data')
DEBUG_DIR = os.environ.get('TOTO_DEBUG_DIR', 'debug')
HISTORY_DIR = os.path.join(DATA_DIR, 'history')   # натрупва всички тиражи по игра
HISTORY_MAX = int(os.environ.get('TOTO_HISTORY_MAX', '2000'))
PROXY = os.environ.get('TOTO_PROXY', '').strip()
COOKIE = os.environ.get('TOTO_COOKIE', '').strip()              # бисквитки от браузъра: "uzmx=...; uzmxj=...; ..."
IMPERSONATE = os.environ.get('TOTO_IMPERSONATE', '').strip()    # напр. chrome -> curl_cffi с TLS fingerprint на Chrome
PAUSE_SECONDS = float(os.environ.get('TOTO_PAUSE', '2'))
REQUEST_TIMEOUT = (10, 30)          # (connect, read)
STALE_AFTER_DAYS = 8                # тиражите са чт и нд -> над 8 дни без нов е аномалия

GAMES = {
    '6x49':     'toto_6x49',
    '6x42':     'toto_6x42',
    '5x35':     'toto_5x35',
    'joker':    'joker',
    'rojdenden':'rojdenden',
    'zodiak':   'zodiak',
    '13t1':     'toto1_13sreshti',
    '12t1':     'toto1_12tip',
    '10t1':     'toto1_10ot10',
}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _supported_encodings():
    """Обявяваме 'br' САМО ако можем да го разкомпресираме.
    Старият код винаги пращаше 'gzip, deflate, br' без инсталиран brotli ->
    при brotli отговор requests връща компресирани байтове и 'tir_result'
    "липсва", въпреки че страницата е наред."""
    encodings = ['gzip', 'deflate']
    for mod in ('brotli', 'brotlicffi'):
        try:
            __import__(mod)
            encodings.append('br')
            break
        except ImportError:
            continue
    return ', '.join(encodings)


DEFAULT_USER_AGENTS = [
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'toto-results-fetcher/2.0 (+https://github.com/gspetrov96-oss/toto-results)',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0',
]
# TOTO_USER_AGENTS може да е зададена, но празна (secret без стойност) -> ползваме вградените
UA_PROFILES = [p.strip() for p in os.environ.get('TOTO_USER_AGENTS', '').split('|') if p.strip()] or DEFAULT_USER_AGENTS

HEADERS = {
    'User-Agent': UA_PROFILES[0],
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': _supported_encodings(),
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Cache-Control': 'no-cache',
    'Pragma': 'no-cache',
}

log = logging.getLogger('toto')


def build_session():
    s = None
    if IMPERSONATE:
        try:
            from curl_cffi import requests as cffi_requests
            s = cffi_requests.Session(impersonate=IMPERSONATE)
            log.info("Transport: curl_cffi, impersonate=%s (TLS fingerprint на браузър)", IMPERSONATE)
        except ImportError:
            log.warning("TOTO_IMPERSONATE е зададен, но curl_cffi липсва (pip install curl_cffi) - ползвам requests")
        except Exception as e:
            log.warning("curl_cffi не може да стартира (%s) - ползвам requests", e)
    if s is not None:
        s.headers.update(HEADERS)
        if COOKIE:
            s.headers['Cookie'] = COOKIE
            log.info("Cookie header зададен (%d знака)", len(COOKIE))
        if PROXY:
            s.proxies.update({'http': PROXY, 'https': PROXY})
        return s
    s = requests.Session()
    s.headers.update(HEADERS)
    if COOKIE:
        s.headers['Cookie'] = COOKIE
        log.info("Cookie header зададен (%d знака)", len(COOKIE))
    retry = Retry(
        total=3, connect=3, read=2,
        backoff_factor=3,                       # 0s, 6s, 12s
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(['GET']),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount('https://', adapter)
    s.mount('http://', adapter)
    if PROXY:
        s.proxies.update({'http': PROXY, 'https': PROXY})
        log.info("Using proxy from TOTO_PROXY (%s)", re.sub(r'//[^@]*@', '//***@', PROXY))
    return s


CHALLENGE_MARKERS = ('cf-chl', 'challenge-platform', 'Just a moment', 'Attention Required',
                     'captcha', 'Access denied', 'Request unsuccessful', 'Incapsula',
                     'Radware', 'rdwr', 'perfdrive', 'validate.perfdrive', 'bot manager')


class FetchResult:
    def __init__(self, url):
        self.url = url
        self.html = None
        self.ok = False
        self.reason = ''
        self.message = ''
        self.http_status = None
        self.diag = {}

    def to_dict(self):
        d = {'url': self.url, 'ok': self.ok, 'reason': self.reason,
             'message': self.message, 'http_status': self.http_status}
        d.update(self.diag)
        return d


def _save_debug(name, content):
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        path = os.path.join(DEBUG_DIR, f'{name}.html')
        with open(path, 'w', encoding='utf-8', errors='replace') as f:
            f.write(content[:300_000])
        log.info("  Debug copy saved: %s", path)
    except Exception as e:                       # debug never breaks the run
        log.warning("  Could not save debug copy: %s", e)


BROWSER_MODE = os.environ.get('TOTO_BROWSER', '').strip() == '1'
_BROWSER = {'pw': None, 'browser': None, 'ctx': None, 'failed': False}


def browser_fetch(url, require_marker='tir_result', wait_s=25):
    """Експериментално: отваря страницата с истински браузър (Playwright/Chromium), за да премине
    JavaScript challenge-а на WAF-а. Включва се с TOTO_BROWSER=1; изисква `pip install playwright`
    и `playwright install chromium`. Връща HTML или None."""
    if _BROWSER['failed']:
        return None
    try:
        if _BROWSER['ctx'] is None:
            from playwright.sync_api import sync_playwright
            _BROWSER['pw'] = sync_playwright().start()
            _BROWSER['browser'] = _BROWSER['pw'].chromium.launch(
                headless=True, args=['--disable-blink-features=AutomationControlled'])
            _BROWSER['ctx'] = _BROWSER['browser'].new_context(
                user_agent=DEFAULT_USER_AGENTS[0], locale='bg-BG', viewport={'width': 1366, 'height': 900})
            log.info("  браузър: Chromium стартиран (TOTO_BROWSER=1)")
        page = _BROWSER['ctx'].new_page()
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=wait_s * 1000)
            marker = f'.{require_marker}' if require_marker else 'body'
            try:
                page.wait_for_selector(marker, timeout=wait_s * 1000)
            except Exception:
                pass
            html = page.content()
        finally:
            page.close()
        if require_marker and require_marker not in html:
            log.error("  браузър: challenge-ът не е преминат за %s (<title>: %s)", url,
                      (re.search(r'<title[^>]*>(.*?)</title>', html, re.S | re.I) or [None, '-'])[1])
            return None
        log.info("  браузър: OK %s (%d bytes)", url, len(html))
        return html
    except Exception as e:
        _BROWSER['failed'] = True
        log.error("  браузър: недостъпен (%s). Инсталирай: pip install playwright && playwright install chromium", e)
        return None


def browser_close():
    try:
        if _BROWSER['browser']: _BROWSER['browser'].close()
        if _BROWSER['pw']: _BROWSER['pw'].stop()
    except Exception:
        pass


_WAF_BLOCKED = {'all_ua': False}      # щом всички UA профили са блокирани -> не ги въртим повече


def fetch(session, url, debug_name, require_marker='tir_result'):
    """Опитва всички UA профили при антибот блок; при пълен блок пробва браузър (ако е включен)."""
    res = None
    profiles = UA_PROFILES[:1] if _WAF_BLOCKED['all_ua'] else UA_PROFILES
    for i, ua in enumerate(profiles):
        session.headers['User-Agent'] = ua
        res = _fetch_once(session, url, debug_name, require_marker)
        if res.ok or res.reason not in ('bot_challenge', 'blocked_or_rate_limited'):
            break
        if i + 1 < len(profiles):
            log.warning("  блок с UA #%d -> опит с UA #%d (%s)", i + 1, i + 2, profiles[i + 1][:40])
            time.sleep(PAUSE_SECONDS)
    if not res.ok and res.reason in ('bot_challenge', 'blocked_or_rate_limited'):
        if not _WAF_BLOCKED['all_ua']:
            _WAF_BLOCKED['all_ua'] = True
            log.warning("  всички UA профили са блокирани - спирам ротацията за останалите URL-и")
        if BROWSER_MODE:
            html = browser_fetch(url, require_marker)
            if html:
                res.html, res.ok, res.reason, res.message = html, True, '', ''
                res.diag['via'] = 'browser'
    res.diag['user_agent'] = session.headers.get('User-Agent', '')[:60]
    return res


def _fetch_once(session, url, debug_name, require_marker='tir_result'):
    """Връща FetchResult с ясна причина при неуспех."""
    res = FetchResult(url)
    log.info("Fetching %s", url)
    t0 = time.time()
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    except requests.exceptions.Timeout as e:
        res.reason, res.message = 'timeout', f'{type(e).__name__}: {e}'
        log.error("  TIMEOUT след %.1fs: %s (възможно блокиране на IP адресите на GitHub)",
                  time.time() - t0, e)
        return res
    except requests.exceptions.SSLError as e:
        res.reason, res.message = 'ssl_error', str(e)
        log.error("  SSL ERROR: %s", e)
        return res
    except requests.exceptions.ConnectionError as e:
        # след изчерпани retries urllib3 обвива timeout-а в ConnectionError
        if 'timed out' in str(e).lower() or 'timeouterror' in str(e).lower():
            res.reason, res.message = 'timeout', str(e)
            log.error("  TIMEOUT след %.1fs (с повторни опити): %s (възможно блокиране на IP адресите на GitHub)",
                      time.time() - t0, e)
        else:
            res.reason, res.message = 'connection_error', str(e)
            log.error("  CONNECTION ERROR: %s", e)
        return res
    except requests.exceptions.RequestException as e:
        res.reason, res.message = 'request_error', f'{type(e).__name__}: {e}'
        log.error("  REQUEST ERROR: %s", res.message)
        return res
    except Exception as e:                       # curl_cffi и други транспорти
        msg = str(e).lower()
        res.reason = 'timeout' if ('timed out' in msg or 'timeout' in msg) else 'request_error'
        res.message = f'{type(e).__name__}: {e}'
        log.error("  %s: %s", res.reason.upper(), res.message)
        return res

    res.http_status = r.status_code
    hdr = getattr(r, 'headers', {}) or {}
    res.diag = {
        'final_url': getattr(r, 'url', url),
        'bytes': len(r.content),
        'elapsed_s': round(time.time() - t0, 2),
        'content_type': hdr.get('Content-Type', '') or hdr.get('content-type', ''),
        'content_encoding': hdr.get('Content-Encoding', '') or hdr.get('content-encoding', ''),
        'server': hdr.get('Server', '') or hdr.get('server', ''),
        'cf_ray': hdr.get('CF-RAY', '') or hdr.get('cf-ray', ''),
    }
    log.info("  Status %s | %s bytes | encoding=%r | type=%r | server=%r | %.1fs",
             r.status_code, res.diag['bytes'], res.diag['content_encoding'],
             res.diag['content_type'], res.diag['server'], res.diag['elapsed_s'])

    try:
        if 'charset' not in res.diag['content_type'].lower():
            r.encoding = 'utf-8'
        text = r.text
    except Exception:
        text = r.content.decode('utf-8', errors='replace')

    if r.status_code != 200:
        if r.status_code in (401, 403, 429, 503):
            res.reason = 'blocked_or_rate_limited'
            res.message = f'HTTP {r.status_code} - сървърът отказва достъп (WAF/антибот/IP блок)'
        else:
            res.reason, res.message = 'http_error', f'HTTP {r.status_code}'
        log.error("  %s", res.message)
        _save_debug(debug_name, text)
        return res

    # Неразкомпресирано тяло (напр. br без brotli) -> много заместващи символи, няма '<'
    sample = text[:2000]
    if sample and ('<' not in sample or sample.count('\ufffd') > len(sample) * 0.05):
        res.reason = 'undecodable_body'
        res.message = (f'Тялото не е четим HTML (Content-Encoding={res.diag["content_encoding"]!r}). '
                       f'Инсталирай brotli или махни "br" от Accept-Encoding.')
        log.error("  %s", res.message)
        return res

    low = text[:5000].lower()
    is_challenge = any(m.lower() in low for m in CHALLENGE_MARKERS) and 'tir_result' not in text
    if is_challenge:
        res.reason = 'bot_challenge'
        title = re.search(r'<title[^>]*>(.*?)</title>', text, re.S | re.I)
        res.message = ('Антибот защита (WAF) върна challenge страница вместо резултати - IP адресът е блокиран. '
                       f'<title>: {title.group(1).strip()[:80] if title else "-"}. '
                       'Решение: TOTO_PROXY secret или изпълнение на скрипта от друг сървър (виж README).')
        log.error("  %s", res.message)
        _save_debug(debug_name, text)
        return res
    if require_marker and require_marker not in text:
        res.reason = 'layout_changed'
        title = re.search(r'<title[^>]*>(.*?)</title>', text, re.S | re.I)
        res.message = (f'Липсва "{require_marker}" - вероятно е сменена HTML структурата. '
                       f'<title>: {title.group(1).strip()[:120] if title else "-"}')
        log.error("  %s", res.message)
        _save_debug(debug_name, text)
        return res

    res.html, res.ok = text, True
    return res


# ---------------------------------------------------------------------------
# Парсване (непроменено спрямо оригинала)
# ---------------------------------------------------------------------------

def get_ball_whites(tag):
    """Взима всички ball-white числа от HTML таг"""
    return [s.get_text(strip=True) for s in tag.find_all('span', class_='ball-white')]


def extract_tir_result(soup):
    return soup.find('div', class_='tir_result')


def get_jackpot(tir):
    jp_div = tir.find('div', class_='tir_jackpot')
    if not jp_div:
        return ''
    sum_div = jp_div.find('div', class_=lambda c: c and 'sum' in c and 'text-right' in c)
    if sum_div:
        return sum_div.get_text(separator=' ', strip=True)
    return ''


def get_winnings(tir):
    pechalbi = tir.find('div', class_=lambda c: c and 'tir_pechalbi' in c)
    if not pechalbi:
        return []
    rows = []
    for i, tr in enumerate(pechalbi.find_all('tr')):
        if i == 0:
            continue  # header
        tds = [td.get_text(separator=' ', strip=True) for td in tr.find_all('td')]
        if len(tds) >= 4 and tds[0]:
            rows.append({'label': tds[0], 'count': tds[1], 'amount': tds[2], 'total': tds[3]})
    return rows


def parse_standard(soup, game):
    tir = extract_tir_result(soup)
    if not tir:
        return None

    title_tag = tir.find('h2', class_='tir_title')
    title = title_tag.get_text(strip=True) if title_tag else ''

    # Намираме всички tir_numbers блокове
    tir_numbers = tir.find_all('div', class_=lambda c: c and 'tir_numbers' in c)
    tir_jackpots = tir.find_all('div', class_=lambda c: c and 'tir_jackpot' in c)
    tir_pechalbi = tir.find_all('div', class_=lambda c: c and 'tir_pechalbi' in c)

    # За 5x35 може да има teglene_title разделители
    teglene_titles = tir.find_all('div', class_='teglene_title')
    has_multiple = len(teglene_titles) > 1

    drawings = []

    if has_multiple:
        for i in range(len(tir_numbers)):
            nums = get_ball_whites(tir_numbers[i]) if i < len(tir_numbers) else []
            jp   = ''
            if i < len(tir_jackpots):
                sum_d = tir_jackpots[i].find('div', class_=lambda c: c and 'sum' in c and 'text-right' in c)
                if sum_d:
                    jp = sum_d.get_text(separator=' ', strip=True)
            wins = []
            if i < len(tir_pechalbi):
                for j, tr in enumerate(tir_pechalbi[i].find_all('tr')):
                    if j == 0: continue
                    tds = [td.get_text(separator=' ', strip=True) for td in tr.find_all('td')]
                    if len(tds) >= 4 and tds[0]:
                        wins.append({'label': tds[0], 'count': tds[1], 'amount': tds[2], 'total': tds[3]})
            if nums:
                drawings.append({'numbers': nums, 'jackpot': jp, 'winnings': wins, 'type': 'standard'})
    else:
        nums = get_ball_whites(tir_numbers[0]) if tir_numbers else []
        jp   = get_jackpot(tir)
        wins = get_winnings(tir)
        if nums:
            drawings.append({'numbers': nums, 'jackpot': jp, 'winnings': wins, 'type': 'standard'})

    return {'game': game, 'title': title, 'drawings': drawings, 'fetched': int(time.time())}


def parse_joker(soup, game):
    tir = extract_tir_result(soup)
    if not tir:
        return None

    title_tag = tir.find('h2', class_='tir_title')
    title = title_tag.get_text(strip=True) if title_tag else ''

    tir_numbers = tir.find_all('div', class_=lambda c: c and 'tir_numbers' in c)

    positions = get_ball_whites(tir_numbers[0]) if len(tir_numbers) > 0 else []
    numbers   = get_ball_whites(tir_numbers[1]) if len(tir_numbers) > 1 else []
    jp        = get_jackpot(tir)
    wins      = get_winnings(tir)

    drawings = []
    if positions or numbers:
        drawings.append({
            'numbers': numbers, 'positions': positions,
            'jackpot': jp, 'winnings': wins, 'type': 'joker'
        })

    return {'game': game, 'title': title, 'drawings': drawings, 'fetched': int(time.time())}


def parse_zodiak(soup, game):
    tir = extract_tir_result(soup)
    if not tir:
        return None

    title_tag = tir.find('h2', class_='tir_title')
    title = title_tag.get_text(strip=True) if title_tag else ''

    tir_numbers = tir.find_all('div', class_=lambda c: c and 'tir_numbers' in c)

    numbers = get_ball_whites(tir_numbers[0]) if len(tir_numbers) > 0 else []
    zodia_nums = get_ball_whites(tir_numbers[1]) if len(tir_numbers) > 1 else []
    zodia = zodia_nums[0] if zodia_nums else ''
    jp    = get_jackpot(tir)
    wins  = get_winnings(tir)

    drawings = []
    if numbers:
        drawings.append({
            'numbers': numbers, 'zodia': zodia,
            'jackpot': jp, 'winnings': wins, 'type': 'zodiak'
        })

    return {'game': game, 'title': title, 'drawings': drawings, 'fetched': int(time.time())}


def parse_rojdenden(soup, game):
    tir = extract_tir_result(soup)
    if not tir:
        return None

    title_tag = tir.find('h2', class_='tir_title')
    title = title_tag.get_text(strip=True) if title_tag else ''

    ymdd = []
    # span.ymwd + span[style] (ball-small)
    for span in tir.find_all('span', class_='ymwd'):
        label = span.get_text(strip=True)
        sib = span.find_next_sibling('span')
        if sib:
            ymdd.append({'label': label, 'value': sib.get_text(strip=True)})

    # fallback: div.ymdd + div.value
    if not ymdd:
        for d in tir.find_all('div', class_='ymdd'):
            label = d.get_text(strip=True)
            val_div = d.find_next_sibling('div', class_='value')
            if val_div:
                ymdd.append({'label': label, 'value': val_div.get_text(strip=True)})

    jp   = get_jackpot(tir)
    wins = get_winnings(tir)

    drawings = []
    if ymdd:
        drawings.append({
            'numbers': [y['value'] for y in ymdd],
            'ymdd': ymdd, 'jackpot': jp, 'winnings': wins, 'type': 'rojdenden'
        })

    return {'game': game, 'title': title, 'drawings': drawings, 'fetched': int(time.time())}


def parse_toto1( soup, game ):
    """Тото 1 — div.numbers + div.xses (1/X/2 резултати)"""
    tir = extract_tir_result(soup)
    if not tir:
        return None

    title_tag = tir.find('h2', class_='tir_title')
    title = title_tag.get_text(strip=True) if title_tag else ''

    # Извличаме двойките number/xses
    matches = []
    for cell in tir.find_all('div', style=lambda s: s and 'display:inline-block' in s):
        num_div = cell.find('div', class_='numbers')
        xse_div = cell.find('div', class_='xses')
        if num_div and xse_div:
            matches.append({
                'match':  num_div.get_text(strip=True),
                'result': xse_div.get_text(strip=True).lower(),
            })

    jp    = get_jackpot(tir)
    wins  = get_winnings(tir)

    drawings = []
    if matches:
        drawings.append({
            'matches':  matches,
            'jackpot':  jp,
            'winnings': wins,
            'type':     'toto1',
        })

    return {'game': game, 'title': title, 'drawings': drawings, 'fetched': int(time.time())}


PARSERS = {
    '6x49': parse_standard,
    '6x42': parse_standard,
    '5x35': parse_standard,
    'joker': parse_joker,
    'zodiak': parse_zodiak,
    'rojdenden': parse_rojdenden,
    '13t1': parse_toto1,
    '12t1': parse_toto1,
    '10t1': parse_toto1,
}


# ---------------------------------------------------------------------------
# Тираж / дата / свежест
# ---------------------------------------------------------------------------

DRAW_RE = re.compile(r'Тираж\s*№?\s*(\d{1,3})\s*[-–—]\s*(\d{1,2})\.(\d{1,2})\.(\d{4})')


def parse_draw_meta(*texts):
    """'Тираж 86 - 01.11.2026' -> (86, date(2026,11,1)). Връща най-новия намерен."""
    best = None
    for t in texts:
        if not t:
            continue
        for m in DRAW_RE.finditer(t):
            try:
                d = date(int(m.group(4)), int(m.group(3)), int(m.group(2)))
            except ValueError:
                continue
            key = (d, int(m.group(1)))
            if best is None or key > best:
                best = key
    return (best[1], best[0]) if best else (None, None)


def page_draw_meta(soup):
    """Тираж/дата от h2.tir_title, после от <title>/og:title като резерва."""
    h2 = soup.find('h2', class_='tir_title')
    og = soup.find('meta', attrs={'property': 'og:title'}) or soup.find('meta', attrs={'name': 'og:title'})
    return parse_draw_meta(
        h2.get_text(' ', strip=True) if h2 else '',
        soup.title.get_text(' ', strip=True) if soup.title else '',
        og.get('content', '') if og else '',
    )


def saved_draw_meta(data):
    if not data:
        return None, None
    if data.get('draw_date'):
        try:
            return data.get('draw'), date.fromisoformat(data['draw_date'])
        except ValueError:
            pass
    return parse_draw_meta(data.get('title', ''))


def load_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        log.warning("Cannot read %s: %s", path, e)
        return None


def gh(level, title, msg):
    """GitHub Actions анотация (вижда се в обобщението на рън-а)."""
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print(f"::{level} title={title}::{msg}", flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def history_entry(data):
    """Компактен запис за историята: само това, което архивът ползва."""
    e = {'draw': data.get('draw'), 'draw_date': data.get('draw_date'), 'title': data.get('title', '')}
    dr = data.get('drawings') or []
    kind = dr[0].get('type', 'standard') if dr else 'standard'
    e['type'] = kind
    if kind == 'toto1':
        e['matches'] = dr[0].get('matches', [])
    else:
        e['numbers'] = [d.get('numbers', []) for d in dr]      # списък от тегления
    if kind == 'joker':
        e['positions'] = dr[0].get('positions', [])
    if kind == 'zodiak':
        e['zodia'] = dr[0].get('zodia', '')
    if kind == 'rojdenden':
        e['ymdd'] = dr[0].get('ymdd', [])
    e['jackpot'] = dr[0].get('jackpot', '') if dr else ''
    return e


def update_history(filename, data):
    """Добавя тиража в data/history/<file>.json, ако още го няма (ключ: дата+номер).
    Връща True ако файлът е променен."""
    if not data.get('draw_date'):
        return False
    os.makedirs(HISTORY_DIR, exist_ok=True)
    path = os.path.join(HISTORY_DIR, f'{filename}.json')
    hist = load_json(path) or {'game': data.get('game'), 'draws': []}
    entry = history_entry(data)
    key = (entry['draw_date'], entry['draw'])
    draws = [d for d in hist.get('draws', []) if (d.get('draw_date'), d.get('draw')) != key]
    changed = len(draws) == len(hist.get('draws', []))  # нов запис
    old = next((d for d in hist.get('draws', []) if (d.get('draw_date'), d.get('draw')) == key), None)
    if old is not None and old == entry:
        return False
    draws.append(entry)
    draws.sort(key=lambda d: (d.get('draw_date') or '', d.get('draw') or 0), reverse=True)
    hist['game'] = data.get('game')
    hist['updated'] = datetime.now(timezone.utc).isoformat()
    hist['draws'] = draws[:HISTORY_MAX]
    hist['count'] = len(hist['draws'])
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)
    log.info("  history: %s (%d тиража, %s)", path, hist['count'], 'нов' if changed else 'обновен')
    return True


def process_game(session, game, filename, latest_ref):
    """Връща (status, detail). status: ok | kept | failed"""
    detail = {'file': f'{filename}.json'}
    out_path = os.path.join(DATA_DIR, f'{filename}.json')
    saved = load_json(out_path)
    s_num, s_date = saved_draw_meta(saved)
    if s_date:
        detail['saved_draw'] = s_num
        detail['saved_draw_date'] = s_date.isoformat()

    candidates = [BASE_URL + game]
    data, used_url, fr = None, None, None

    for attempt, url in enumerate(candidates):
        fr = fetch(session, url, debug_name=game)
        detail['fetch'] = fr.to_dict()
        if not fr.ok:
            continue
        soup = BeautifulSoup(fr.html, 'lxml')
        parsed = PARSERS[game](soup, game)
        if not parsed or not parsed['drawings']:
            fr.ok, fr.reason = False, 'parse_failed'
            fr.message = 'tir_result е намерен, но не са извлечени числа - сменена вътрешна структура'
            detail['fetch'] = fr.to_dict()
            log.error("  %s", fr.message)
            _save_debug(game, fr.html)
            continue
        num, d = page_draw_meta(soup)
        if num is None:
            num, d = parse_draw_meta(parsed.get('title', ''))
        parsed['draw'] = num
        parsed['draw_date'] = d.isoformat() if d else None
        parsed['source_url'] = url
        data, used_url = parsed, url

        # Страницата "последен тираж" изостава от началната (кеш)? -> архивен URL
        if (attempt == 0 and latest_ref and d and num
                and (d, num) < (latest_ref[1], latest_ref[0])):
            arch = f"{BASE_URL}{game}/{latest_ref[1].year}-{latest_ref[0]}"
            log.warning("  %s показва тираж %s (%s), а най-новият е %s (%s) -> опит с архивен URL %s",
                        game, num, d, latest_ref[0], latest_ref[1], arch)
            candidates.append(arch)
            time.sleep(PAUSE_SECONDS)
            continue
        break

    if not data:
        reason = fr.reason if fr else 'unknown'
        msg = fr.message if fr else ''
        detail.update({'status': 'failed', 'reason': reason, 'message': msg})
        gh('error', f'{game}: {reason}', msg or reason)
        return 'failed', detail

    new_d, new_n = data.get('draw_date'), data.get('draw')
    detail.update({'draw': new_n, 'draw_date': new_d, 'source_url': used_url,
                   'drawings': len(data['drawings'])})

    # Историята приема всеки валиден тираж (и по-стар - backfill)
    try:
        update_history(filename, data)
    except Exception as e:
        log.warning("  history update failed: %s", e)

    # Никога не връщаме назад: ако източникът е по-стар от записаното, пазим записаното
    if s_date and new_d and date.fromisoformat(new_d) < s_date:
        msg = f'Източникът връща тираж {new_n} ({new_d}), по-стар от записания {s_num} ({s_date}) - файлът не е презаписан'
        log.warning("  %s", msg)
        gh('warning', f'{game}: stale source', msg)
        detail.update({'status': 'kept', 'reason': 'source_older_than_saved', 'message': msg})
        return 'kept', detail

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    if new_d:
        age = (datetime.now(timezone.utc).date() - date.fromisoformat(new_d)).days
        detail['draw_age_days'] = age
        if age > STALE_AFTER_DAYS:
            msg = f'Последният тираж в източника е на {age} дни ({new_d}) - провери info.toto.bg'
            log.warning("  %s", msg)
            gh('warning', f'{game}: old draw', msg)
            detail['warning'] = msg
    else:
        log.warning("  Не може да се определи тираж/дата от заглавието: %r", data.get('title'))

    log.info("  OK %s: %s | тираж %s, %s | %d drawing(s)",
             game, data['title'], new_n, new_d, len(data['drawings']))
    detail.update({'status': 'ok', 'reason': ''})
    return 'ok', detail


def write_step_summary(summary):
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not path:
        return
    lines = ['## Тото резултати', '',
             f"Най-нов тираж на info.toto.bg: **{summary.get('source_latest') or 'неизвестен'}**", '',
             '| Игра | Статус | Тираж | Дата | Причина |', '|---|---|---|---|---|']
    for g, d in summary['details'].items():
        icon = {'ok': '✅', 'kept': '⚠️', 'failed': '❌'}.get(d.get('status'), '?')
        lines.append(f"| {g} | {icon} {d.get('status')} | {d.get('draw') or d.get('saved_draw') or ''} "
                     f"| {d.get('draw_date') or d.get('saved_draw_date') or ''} "
                     f"| {d.get('message') or d.get('warning') or ''} |")
    try:
        with open(path, 'a', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    except Exception:
        pass


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)-7s %(message)s',
                        stream=sys.stdout)
    os.makedirs(DATA_DIR, exist_ok=True)
    log.info("requests %s | Accept-Encoding: %s", requests.__version__, HEADERS['Accept-Encoding'])

    session = build_session()
    prev_summary = load_json(os.path.join(DATA_DIR, 'summary.json')) or {}
    last_success = dict(prev_summary.get('last_success') or {})

    # Референтен "последен тираж" от началната страница (за откриване на изоставане/кеш)
    latest_ref = None
    home = fetch(session, HOME_URL, debug_name='home', require_marker=None)
    if home.ok:
        num, d = parse_draw_meta(BeautifulSoup(home.html, 'lxml').get_text(' ', strip=True))
        if num:
            latest_ref = (num, d)
            log.info("Най-нов тираж според началната страница: %s (%s)", num, d)
    else:
        log.warning("Началната страница е недостъпна (%s) - продължаваме без референтен тираж", home.reason)
    time.sleep(PAUSE_SECONDS)

    results, errors, details = {}, [], {}
    for game, filename in GAMES.items():
        status, detail = process_game(session, game, filename, latest_ref)
        details[game] = detail
        if status == 'ok':
            results[game] = True
            last_success[game] = datetime.now(timezone.utc).isoformat()
        elif status == 'kept':
            results[game] = True
        else:
            errors.append(game)
        time.sleep(PAUSE_SECONDS)   # пауза между заявките (и при грешка)

    # Обединен файл: плъгинът прави 1 заявка вместо 9
    combined = {'updated': datetime.now(timezone.utc).isoformat(), 'games': {}}
    for game, filename in GAMES.items():
        d = load_json(os.path.join(DATA_DIR, f'{filename}.json'))
        if d:
            combined['games'][game] = d
    with open(os.path.join(DATA_DIR, 'all.json'), 'w', encoding='utf-8') as f:
        json.dump(combined, f, ensure_ascii=False, indent=1)

    reasons = sorted({details[g].get('reason') for g in errors if details[g].get('reason')})
    summary = {
        'updated': datetime.now(timezone.utc).isoformat(),
        'games': {g: bool(results.get(g)) for g in GAMES},
        'errors': errors,
        'error_reasons': reasons,
        'source_latest': (f"Тираж {latest_ref[0]} - {latest_ref[1].strftime('%d.%m.%Y')}"
                          if latest_ref else None),
        'home_page': home.to_dict(),
        'last_success': last_success,
        'details': details,
    }
    with open(os.path.join(DATA_DIR, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    write_step_summary(summary)

    browser_close()
    ok = len(GAMES) - len(errors)
    log.info("Done. Success: %d/%d, Errors: %s, Reasons: %s", ok, len(GAMES), errors, reasons)
    if errors:
        gh('error', 'Toto fetch', f"{len(errors)}/{len(GAMES)} игри неуспешни: {', '.join(errors)} ({', '.join(reasons)})")
        return 2 if ok == 0 else 1
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:                      # срив -> видим в анотациите и в summary.json
        import traceback
        tb = traceback.format_exc()
        print(tb, file=sys.stderr)
        gh('error', 'fetch_toto.py crash', tb.strip().splitlines()[-1][:300])
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(os.path.join(DATA_DIR, 'summary.json'), 'w', encoding='utf-8') as f:
                json.dump({'updated': datetime.now(timezone.utc).isoformat(), 'games': {g: False for g in GAMES},
                           'errors': list(GAMES), 'error_reasons': ['crash'], 'crash': tb[-2000:]}, f, ensure_ascii=False, indent=2)
        finally:
            sys.exit(3)
