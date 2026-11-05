"""
Офлайн тестове: локален HTTP сървър симулира info.toto.bg.
Пускане: python -m unittest discover -s tests -v
"""
import http.server
import importlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def balls(nums):
    return ''.join(f'<span class="ball-white">{n}</span>' for n in nums)


WIN_TABLE = ('<div class="tir_pechalbi"><table><tr><th>a</th><th>b</th><th>c</th><th>d</th></tr>'
             '<tr><td>6 числа</td><td>0</td><td>0.00 euro</td><td>0.00 euro</td></tr>'
             '<tr><td>5 числа</td><td>39</td><td>978.10 euro</td><td>38 145.90 euro</td></tr></table></div>')
JACKPOT = '<div class="tir_jackpot"><div class="sum text-right">5 637 241.12 euro</div></div>'


def results_page(game, draw, dmy):
    title = f'<h2 class="tir_title">Тираж {draw} - {dmy}</h2>'
    if game in ('6x49', '6x42'):
        body = f'<div class="tir_numbers">{balls([4, 10, 16, 27, 29, 34])}</div>{JACKPOT}{WIN_TABLE}'
    elif game == '5x35':
        body = (f'<div class="teglene_title">1</div><div class="tir_numbers">{balls([12,13,16,19,21])}</div>{WIN_TABLE}'
                f'<div class="teglene_title">2</div><div class="tir_numbers">{balls([7,8,11,20,26])}</div>{WIN_TABLE}')
    elif game == 'joker':
        body = f'<div class="tir_numbers">{balls([2,3,8])}</div><div class="tir_numbers">{balls([4,9,5])}</div>{JACKPOT}{WIN_TABLE}'
    elif game == 'zodiak':
        body = f'<div class="tir_numbers">{balls([14,19,23,28,36])}</div><div class="tir_numbers">{balls([12])}</div>{JACKPOT}{WIN_TABLE}'
    elif game == 'rojdenden':
        body = ''.join(f'<span class="ymwd">{l}</span><span style="x">{v}</span>'
                       for l, v in [('Година', 39), ('Месец', 7), ('Дата', 23), ('Ден', 1)]) + JACKPOT + WIN_TABLE
    else:  # toto1
        body = ''.join(f'<div style="display:inline-block"><div class="numbers">{i}</div><div class="xses">X</div></div>'
                       for i in range(1, 11)) + JACKPOT + WIN_TABLE
    return (f'<html><head><meta charset="utf-8"><title>Тираж {draw} - {dmy}, игра | БСТ</title></head>'
            f'<body><div class="tir_result">{title}{body}</div></body></html>')


HOME = '<html><head><title>Начало | БСТ</title></head><body><h3>Тото 2</h3>Тираж 86 - 01.11.2026 ' \
       '<a>ТОТО Live | Тираж 87| 05.11.2026</a></body></html>'


class MockSite(http.server.BaseHTTPRequestHandler):
    mode = 'ok'            # ok | 403 | br_only | layout | challenge | stale_latest | old_source
    hits = []

    def log_message(self, *a):
        pass

    def _send(self, code, text, enc=None):
        raw = text.encode('utf-8')
        ae = self.headers.get('Accept-Encoding', '')
        if enc is None and 'br' in ae:
            # Сървър, който предпочита brotli. Без brotli на клиента -> нечетимо.
            raw, enc = os.urandom(32) + zlib.compress(raw), 'br'
        elif enc is None and 'gzip' in ae:
            import gzip
            raw, enc = gzip.compress(raw), 'gzip'
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=UTF-8')
        if enc:
            self.send_header('Content-Encoding', enc)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        MockSite.hits.append(self.path)
        m = MockSite.mode
        if self.path == '/':
            return self._send(200, HOME)
        if m == '403':
            return self._send(403, '<html>Forbidden</html>')
        if m == 'layout':
            return self._send(200, '<html><head><title>Нов дизайн</title></head><body><div class="results-v2"></div></body></html>')
        if m == 'challenge':
            return self._send(200, '<html><head><title>Just a moment...</title></head><body>cf-chl</body></html>')
        parts = self.path.strip('/').split('/')        # results/<game>[/<year-draw>]
        game = parts[1]
        if len(parts) == 3:                             # архивен URL
            draw = int(parts[2].split('-')[1])
            return self._send(200, results_page(game, draw, '01.11.2026' if draw == 86 else '25.10.2026'))
        if m in ('stale_latest', 'old_source'):
            return self._send(200, results_page(game, 84, '25.10.2026'))
        return self._send(200, results_page(game, 86, '01.11.2026'))


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), MockSite)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ.update({
            'TOTO_BASE_URL': f'http://127.0.0.1:{self.port}/results/',
            'TOTO_HOME_URL': f'http://127.0.0.1:{self.port}/',
            'TOTO_DATA_DIR': os.path.join(self.tmp, 'data'),
            'TOTO_DEBUG_DIR': os.path.join(self.tmp, 'debug'),
            'TOTO_PAUSE': '0',
        })
        os.environ.pop('GITHUB_ACTIONS', None)
        MockSite.hits = []
        import fetch_toto
        self.ft = importlib.reload(fetch_toto)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def data(self, name):
        with open(os.path.join(self.tmp, 'data', name), encoding='utf-8') as f:
            return json.load(f)

    def run_main(self, mode):
        MockSite.mode = mode
        return self.ft.main()


class TestFetcher(Base):
    def test_accept_encoding_never_advertises_unsupported_br(self):
        try:
            import brotli  # noqa
            has_br = True
        except ImportError:
            has_br = False
        self.assertEqual('br' in self.ft.HEADERS['Accept-Encoding'], has_br)

    def test_happy_path_all_games(self):
        self.assertEqual(self.run_main('ok'), 0)
        s = self.data('summary.json')
        self.assertEqual(s['errors'], [])
        self.assertTrue(all(s['games'].values()))
        self.assertEqual(s['source_latest'], 'Тираж 86 - 01.11.2026')
        d = self.data('toto_6x49.json')
        # старият формат е запазен
        for k in ('game', 'title', 'drawings', 'fetched'):
            self.assertIn(k, d)
        self.assertEqual(d['drawings'][0]['numbers'], ['4', '10', '16', '27', '29', '34'])
        self.assertEqual(d['drawings'][0]['winnings'][1]['count'], '39')
        self.assertEqual((d['draw'], d['draw_date']), (86, '2026-11-01'))
        self.assertEqual(len(self.data('toto_5x35.json')['drawings']), 2)
        self.assertEqual(self.data('zodiak.json')['drawings'][0]['zodia'], '12')
        self.assertEqual(self.data('joker.json')['drawings'][0]['positions'], ['2', '3', '8'])
        self.assertEqual(len(self.data('rojdenden.json')['drawings'][0]['ymdd']), 4)
        self.assertEqual(len(self.data('toto1_10ot10.json')['drawings'][0]['matches']), 10)

    def test_403_reports_reason_and_keeps_old_files(self):
        self.run_main('ok')
        before = self.data('toto_6x49.json')
        self.assertEqual(self.run_main('403'), 2)
        s = self.data('summary.json')
        self.assertEqual(len(s['errors']), 9)
        self.assertIn('blocked_or_rate_limited', s['error_reasons'])
        self.assertEqual(s['details']['6x49']['fetch']['http_status'], 403)
        self.assertEqual(self.data('toto_6x49.json')['draw'], before['draw'])
        self.assertIn('6x49', s['last_success'])          # пренесено от предишния рън

    def test_layout_change_detected_and_debug_saved(self):
        self.assertEqual(self.run_main('layout'), 2)
        s = self.data('summary.json')
        self.assertEqual(s['error_reasons'], ['layout_changed'])
        self.assertIn('Нов дизайн', s['details']['6x49']['message'])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'debug', '6x49.html')))

    def test_bot_challenge_detected(self):
        self.run_main('challenge')
        self.assertEqual(self.data('summary.json')['error_reasons'], ['bot_challenge'])

    def test_lagging_results_page_uses_archive_url(self):
        self.assertEqual(self.run_main('stale_latest'), 0)
        d = self.data('toto_6x49.json')
        self.assertEqual(d['draw'], 86)
        self.assertTrue(d['source_url'].endswith('/results/6x49/2026-86'))
        self.assertIn('/results/6x49/2026-86', MockSite.hits)

    def test_never_overwrite_with_older_draw(self):
        self.run_main('ok')                               # записан тираж 86
        # началната страница недостъпна -> няма референтен тираж, страницата връща 84
        self.ft.HOME_URL = f'http://127.0.0.1:{self.port}/results/nohome'
        MockSite.mode = 'old_source'
        self.ft.main()
        self.assertEqual(self.data('toto_6x49.json')['draw'], 86)
        self.assertEqual(self.data('summary.json')['details']['6x49']['status'], 'kept')

    def test_timeout_classified(self):
        import socket
        lsock = socket.socket(); lsock.bind(('127.0.0.1', 0)); lsock.listen(5)   # приема, не отговаря
        port = lsock.getsockname()[1]
        try:
            self.ft.REQUEST_TIMEOUT = (1, 1)
            s = self.ft.build_session()
            s.adapters['http://'].max_retries = 0
            r = self.ft.fetch(s, f'http://127.0.0.1:{port}/results/6x49', 'x')
            self.assertFalse(r.ok)
            self.assertEqual(r.reason, 'timeout')
        finally:
            lsock.close()

    def test_old_header_with_br_fails_new_header_works(self):
        """Регресионен тест за основната причина."""
        import requests
        url = f'http://127.0.0.1:{self.port}/results/6x49'
        MockSite.mode = 'ok'
        old = requests.get(url, headers={'Accept-Encoding': 'gzip, deflate, br'})
        new = self.ft.build_session().get(url)
        try:
            import brotli  # noqa
        except ImportError:
            self.assertNotIn('tir_result', old.text)     # старото поведение: "липсва"
        self.assertIn('tir_result', new.text)

    def test_history_and_combined_file(self):
        self.run_main('ok')
        h = self.data(os.path.join('history', 'toto_5x35.json'))
        self.assertEqual(h['count'], 1)
        self.assertEqual(h['draws'][0]['draw'], 86)
        self.assertEqual(len(h['draws'][0]['numbers']), 2)       # две тегления
        self.assertEqual(self.data(os.path.join('history', 'joker.json'))['draws'][0]['positions'], ['2', '3', '8'])
        self.assertEqual(self.data(os.path.join('history', 'toto1_10ot10.json'))['draws'][0]['type'], 'toto1')
        # повторен рън със същия тираж -> без дублиране
        self.run_main('ok')
        self.assertEqual(self.data(os.path.join('history', 'toto_5x35.json'))['count'], 1)
        # по-стар тираж през архивния сценарий остава отделен запис
        MockSite.mode = 'old_source'
        self.ft.HOME_URL = f'http://127.0.0.1:{self.port}/results/nohome'
        self.ft.main()
        h = self.data(os.path.join('history', 'toto_6x49.json'))
        self.assertEqual([d['draw'] for d in h['draws']], [86, 84])
        self.assertEqual(self.data('toto_6x49.json')['draw'], 86)   # latest файлът не е върнат назад
        a = self.data('all.json')
        self.assertEqual(sorted(a['games'].keys()), sorted(self.ft.GAMES.keys()))
        self.assertEqual(a['games']['6x49']['draw'], 86)

    def test_draw_meta_parsing(self):
        p = self.ft.parse_draw_meta
        self.assertEqual(p('Тираж 84 - 25.10.2026, Тото 2 - 6 от 49'), (84, self.ft.date(2026, 10, 25)))
        self.assertEqual(p('ТОТО Live | Тираж 87| 05.11.2026'), (None, None))
        self.assertEqual(p('Тираж 3 - 08.01.2027', 'Тираж 86 - 01.11.2026')[0], 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
