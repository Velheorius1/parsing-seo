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

BACKFILL="15 1 * * * cd /opt/parsing-seo && flock -n /tmp/parsing-seo-purchase-ledger.lock .venv/bin/python3 -m crawler.scripts.purchase_backfill --feed all --incremental --details 2500 --max-minutes 90 >> /var/log/parsing-seo-purchase-ledger.log 2>&1 # parsing-seo purchase ledger daily"
CLASSIFY="5 3 * * * cd /opt/parsing-seo && flock -w 3600 /tmp/parsing-seo-purchase-ledger.lock .venv/bin/python3 -m crawler.scripts.purchase_classify --ai-calls 200 --alert >> /var/log/parsing-seo-purchase-classify.log 2>&1 # parsing-seo purchase classify daily"

for kind in purchase_backfill purchase_classify; do
  if crontab -l | grep -q "$kind"; then
    echo "$kind уже стоит — пропускаю"
    continue
  fi
  if [ "$kind" = purchase_backfill ]; then line="$BACKFILL"; else line="$CLASSIFY"; fi
  ( crontab -l; echo "$line" ) | crontab -
done

echo "поставлено:"
crontab -l | grep -E "purchase_backfill|purchase_classify"
