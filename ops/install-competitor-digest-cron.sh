#!/bin/bash
# Крон сводки по конкурентам (01.10.2026): раз в 3 дня вместо двух недельных.
#
#   ставит   0 5 * * *  scripts/run_competitor_digest.sh   (05:00 UTC = 10:00 Ташкент)
#            ежедневно: «пора ли» решает `--due` по времени последней доставки;
#   убирает  пн 04:00 run_competitor_award_weekly.sh      (монитор площадок — теперь
#                                                          внутри сводки, один ритм)
#            пн 10:30 competitor_wins_weekly --tg          (недельный разбор побед)
#
# Бэкап crontab перед правкой обязателен. Повторный запуск ничего не меняет.
set -euo pipefail

BAK="/root/crontab.bak.$(date +%Y%m%d-%H%M%S)-competitor-digest"
crontab -l > "$BAK"
echo "crontab сохранён: $BAK"

if crontab -l | grep -q "run_competitor_digest.sh"; then
  echo "крон сводки уже стоит — выхожу"
  exit 0
fi

( crontab -l | grep -v "run_competitor_award_weekly.sh" | grep -v "competitor_wins_weekly"
  echo "0 5 * * * flock -n /tmp/parsing-seo-competitor-digest.lock /opt/parsing-seo/scripts/run_competitor_digest.sh >> /var/log/parsing-seo-competitor-digest.log 2>&1 # parsing-seo competitor digest every 3 days (monitor + wins)"
) | crontab -

echo "поставлено:"
crontab -l | grep -E "competitor"
