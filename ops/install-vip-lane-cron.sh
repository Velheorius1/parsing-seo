#!/bin/bash
# Крон ⭐-полосы топ-100 (08.10.2026, фаза 6).
#
#   ставит   35 3 * * *  vip_lane build
#            (03:35 UTC, после разметки журнала в 03:05: индекс заказчиков —
#             ИНН, имена из журнала, кто у кого выигрывал — в data/private/vip_index.json;
#             режим полосы этим не меняется, он в data/private/vip_lane.json)
#
# Бэкап crontab перед правкой обязателен. Повторный запуск ничего не меняет.
set -euo pipefail

BAK="/root/crontab.bak.$(date +%Y%m%d-%H%M%S)-vip-lane"
crontab -l > "$BAK"
echo "crontab сохранён: $BAK"

LINE="35 3 * * * cd /opt/parsing-seo && flock -w 1800 /tmp/parsing-seo-purchase-ledger.lock .venv/bin/python3 -m crawler.scripts.vip_lane build >> /var/log/parsing-seo-vip-lane.log 2>&1 # parsing-seo vip index daily"

if crontab -l | grep -q "crawler.scripts.vip_lane build"; then
  echo "vip_lane build уже стоит — пропускаю"
else
  ( crontab -l; echo "$LINE" ) | crontab -
fi

echo "поставлено:"
crontab -l | grep -E "vip_lane"
