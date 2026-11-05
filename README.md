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

## Ако info.toto.bg блокира GitHub

Добави secret `TOTO_PROXY` (напр. `http://user:pass@host:port`) — скриптът ще ходи през него.

## Тестове

```
python -m unittest discover -s tests -v
```

## Инсталация

1. Fork или клонирай това repo в GitHub
2. Actions се включват автоматично
3. Копирай raw URL-а на JSON файловете в WordPress плъгина
