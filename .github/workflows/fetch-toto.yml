#!/usr/bin/env python3
"""
Тото резултати - fetcher за GitHub Actions
Дърпа от info.toto.bg и записва JSON файлове в data/
"""

import json
import re
import time
import os
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

BASE_URL = 'https://info.toto.bg/results/'
DATA_DIR = 'data'

GAMES = {
    '6x49':     'toto_6x49',
    '6x42':     'toto_6x42',
    '5x35':     'toto_5x35',
    'joker':    'joker',
    'rojdenden':'rojdenden',
    'zodiak':   'zodiak',
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Cache-Control': 'max-age=0',
}

os.makedirs(DATA_DIR, exist_ok=True)

session = requests.Session()
session.headers.update(HEADERS)


def fetch_html(game):
    url = BASE_URL + game
    print(f"Fetching {url}...")
    try:
        r = session.get(url, timeout=30, allow_redirects=True)
        print(f"  Status: {r.status_code}, Size: {len(r.text)} bytes")
        if r.status_code == 200 and 'tir_result' in r.text:
            return r.text
        print(f"  WARNING: tir_result not found in response")
        return None
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


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


PARSERS = {
    '6x49': parse_standard,
    '6x42': parse_standard,
    '5x35': parse_standard,
    'joker': parse_joker,
    'zodiak': parse_zodiak,
    'rojdenden': parse_rojdenden,
}

results = {}
errors  = []

for game, filename in GAMES.items():
    html = fetch_html(game)
    if not html:
        errors.append(game)
        continue

    soup   = BeautifulSoup(html, 'lxml')
    parser = PARSERS[game]
    data   = parser(soup, game)

    if data and data['drawings']:
        print(f"  ✅ {game}: {data['title']}, {len(data['drawings'])} drawing(s)")
        out_path = os.path.join(DATA_DIR, f'{filename}.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        results[game] = data
    else:
        print(f"  ❌ {game}: parse failed")
        errors.append(game)

    time.sleep(2)  # пауза между заявките

# Записваме summary файл
summary = {
    'updated': datetime.now(timezone.utc).isoformat(),
    'games': {g: bool(results.get(g)) for g in GAMES},
    'errors': errors
}
with open(os.path.join(DATA_DIR, 'summary.json'), 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print(f"\n✅ Done. Success: {len(results)}/{len(GAMES)}, Errors: {errors}")
