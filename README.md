# Тото Резултати — GitHub Actions Fetcher

Автоматично дърпа резултати от info.toto.bg след всеки тираж (четвъртък и неделя вечер,
плюс контролни рънове в петък и понеделник). Може да се пусне и ръчно: Actions → Fetch Toto Results → Run workflow.

## Файлове в `data/`

- `toto_6x49.json` — Тото 6/49
- `toto_6x42.json` — Тото 6/42
- `toto_5x35.json` — Тото 5/35
- `joker.json` — Тото Джокер
- `rojdenden.json` — Рожден ден
- `zodiak.json` — Зодиак
- `toto1_13sreshti.json`, `toto1_12tip.json`, `toto1_10ot10.json` — Тото 1
- `summary.json` — статус на последното обновяване
- `all.json` — всички игри в един файл (плъгинът прави 1 заявка вместо 9)
- `history/<игра>.json` — натрупана история на тиражите по игра (ключ: дата + номер; плъгинът импортира
  новите тиражи оттук в архивната таблица, без да скрейпва toto49.com)

Всеки файл с игра има и полетата `draw` (номер на тиража), `draw_date` (ISO дата) и `source_url`.
Останалата структура е непроменена.

## Диагностика

`summary.json` съдържа:
- `games` / `errors` — както преди;
- `error_reasons` — обобщени причини (`timeout`, `blocked_or_rate_limited`, `bot_challenge`,
  `undecodable_body`, `layout_changed`, `parse_failed`, `http_error`…);
- `details` — за всяка игра: HTTP статус, размер, Content-Encoding, сървър, тираж, дата;
- `source_latest` — най-новият тираж според началната страница на info.toto.bg;
- `last_success` — кога всяка игра е обновена успешно за последно.

При грешка рънът завършва с червен статус (GitHub изпраща имейл), а HTML-ът,
който не е разпознат, се качва като artifact `debug-html-N` към рън-а.

При неуспех старите JSON файлове се запазват; файл никога не се презаписва с по-стар тираж.

## Ако info.toto.bg блокира GitHub (Radware Page / bot_challenge)

info.toto.bg е зад Radware Bot Manager, който връща challenge страница (HTTP 200, `<title>Radware Page`)
на заявки от datacenter IP адреси като тези на GitHub Actions. Скриптът го разпознава като `bot_challenge`
и автоматично пробва няколко User-Agent профила (`TOTO_USER_AGENTS`, разделени с `|`). Ако и това не мине:

1. **Прокси:** secret `TOTO_PROXY` = `http://user:pass@host:port` (български/residential proxy или ваш сървър).
2. **Собствен сървър (препоръчително):** качете repo-то на хостинг с python3 и пуснете `cron-run.sh`
   през cPanel Cron. Данните отиват в `TOTO_DATA_DIR` (напр. `public_html/toto-data`), а плъгинът чете от
   `https://вашият-домейн/toto-data`. С `TOTO_GIT_PUSH=1` резултатите се push-ват и в GitHub.
3. **Браузър в GitHub (експериментално):** Repository variable `TOTO_BROWSER=1`. Workflow-ът инсталира Chromium
   (Playwright) и скриптът отваря страниците като истински браузър, за да премине JavaScript challenge-а.
   Може да не мине, ако Radware блокира самия IP адрес; тогава остават вариантите 1 и 2.
4. **Бисквитки + TLS fingerprint (работи и от GitHub):** Radware изисква JavaScript challenge и после издава
   дълготрайни бисквитки (`uzmx`, `uzmxj`, `__uzmlj0`, `__xdst`, ~6 месеца). Копирайте ги от Chrome
   (F12 → Application → Cookies → info.toto.bg) в secret `TOTO_COOKIE` (формат `име=стойност; име2=стойност2`),
   сложете точния User-Agent на същия браузър в `TOTO_USER_AGENTS` и variable `TOTO_IMPERSONATE=chrome`
   (curl_cffi имитира TLS fingerprint-а на Chrome, защото Radware разпознава curl/requests и с валидна бисквитка).
   Локален тест: `test-windows.bat`.
5. Проверка дали даден сървър е блокиран:
   `curl -s -A "Mozilla/5.0" https://info.toto.bg/results/6x49 | grep -c tir_result` → `1` = ОК, `0` = блокиран.

## Тестове

```
python -m unittest discover -s tests -v
```

## Инсталация

1. Fork или клонирай това repo в GitHub
2. Actions се включват автоматично
3. Копирай raw URL-а на JSON файловете в WordPress плъгина
