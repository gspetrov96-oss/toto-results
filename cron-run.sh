#!/usr/bin/env bash
# Изпълнение на fetch_toto.py от ВАШ сървър (cPanel cron), когато info.toto.bg блокира GitHub Actions.
#
# Вариант A (най-прост): данните се записват в публична папка на сайта и плъгинът чете оттам.
#   TOTO_DATA_DIR=/home/USER/public_html/toto-data
#   В плъгина: GitHub Data URL = https://вашият-домейн/toto-data
#
# Вариант B: същото + push към GitHub (нужни са git и Personal Access Token в remote URL),
#   за да остане GitHub repo-то източник за плъгина.
#
# cPanel → Cron Jobs:  35 18,20 * * 0,4   и   15 5,12 * * 1,5   →  /bin/bash /home/USER/toto-results/cron-run.sh
set -euo pipefail

cd "$(dirname "$0")"
export TOTO_DATA_DIR="${TOTO_DATA_DIR:-$PWD/data}"
export TOTO_DEBUG_DIR="${TOTO_DEBUG_DIR:-$PWD/debug}"
PYTHON="${PYTHON:-python3}"
PUSH="${TOTO_GIT_PUSH:-0}"          # 1 = прави git commit + push след успешен рън

mkdir -p "$TOTO_DATA_DIR"
if [ ! -d .venv ]; then
  "$PYTHON" -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

set +e
.venv/bin/python fetch_toto.py >> "$PWD/cron.log" 2>&1
CODE=$?
set -e
echo "$(date -Is) exit=$CODE" >> "$PWD/cron.log"

if [ "$PUSH" = "1" ] && [ "$CODE" != "2" ] && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git add data/
  git diff --staged --quiet || git commit -q -m "Update toto results (server cron)"
  git pull -q --rebase origin main && git push -q origin main
fi
exit $CODE
