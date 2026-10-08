#!/bin/bash
# Крон недельного отчёта топ-100 (08.10.2026, фаза 7).
#
#   ставит   15 6 * * 1  customer_watch_weekly --send
#            (понедельник 06:15 UTC = 11:15 Ташкент: после краула 06:00; flock журнала —
#             не вместе с ночной догрузкой и разметкой; при падении — тревога в Telegram)
#
# Бэкап crontab перед правкой обязателен. Повторный запуск ничего не меняет.
set -euo pipefail

BAK="/root/crontab.bak.$(date +%Y%m%d-%H%M%S)-customer-watch"
crontab -l > "$BAK"
echo "crontab сохранён: $BAK"

LINE="15 6 * * 1 cd /opt/parsing-seo && flock -w 3600 /tmp/parsing-seo-purchase-ledger.lock .venv/bin/python3 -m crawler.scripts.customer_watch_weekly --send >> /var/log/parsing-seo-customer-watch.log 2>&1 # parsing-seo customer watch weekly"

if crontab -l | grep -q "crawler.scripts.customer_watch_weekly"; then
  echo "customer_watch_weekly уже стоит — пропускаю"
else
  ( crontab -l; echo "$LINE" ) | crontab -
fi

echo "поставлено:"
crontab -l | grep -E "customer_watch_weekly"
