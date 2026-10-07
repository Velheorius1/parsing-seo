#!/bin/bash
# Крон журнала закупок (07.10.2026): топ-100 заказчиков и «купили наше мимо нас».
#
#   ставит   15 1 * * *  purchase_backfill --feed all --incremental --details 2500 --max-minutes 90
#            (01:15 UTC: последние 7 дней всех лент + до 2500 карточек ebirja и
#             деталей «печатных» прямых закупок, новые первыми; flock — не в два
#             потока с ручной догрузкой истории)
#
# Бэкап crontab перед правкой обязателен. Повторный запуск ничего не меняет.
set -euo pipefail

BAK="/root/crontab.bak.$(date +%Y%m%d-%H%M%S)-purchase-ledger"
crontab -l > "$BAK"
echo "crontab сохранён: $BAK"

if crontab -l | grep -q "purchase_backfill"; then
  echo "крон журнала закупок уже стоит — выхожу"
  exit 0
fi

( crontab -l
  echo "15 1 * * * cd /opt/parsing-seo && flock -n /tmp/parsing-seo-purchase-ledger.lock .venv/bin/python3 -m crawler.scripts.purchase_backfill --feed all --incremental --details 2500 --max-minutes 90 >> /var/log/parsing-seo-purchase-ledger.log 2>&1 # parsing-seo purchase ledger daily"
) | crontab -

echo "поставлено:"
crontab -l | grep -E "purchase_backfill"
