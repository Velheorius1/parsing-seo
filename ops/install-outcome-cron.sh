#!/bin/bash
# Кроны воронки исхода (20.08.2026).
#
#   sync   — ежедневно сшивает алерты с уже собранными фидами площадки.
#            Ничего не выкачивает, стоит ноль, идемпотентен (повторный прогон
#            пишет 0 строк — это запинено тестом).
#   report — понедельник, воронка алерт → взялись → чем кончилось.
#            Ставится ПОСЛЕ playbook_refine (10:00), чтобы недельные отчёты
#            не приходили вперемешку.
#   nudge  — понедельник, вопрос по лотам с отметкой «подал заявку» и без
#            исхода. Молчит, когда спрашивать нечего.
#
# Бэкап crontab перед правкой обязателен.
set -euo pipefail

BAK="/root/crontab.bak.$(date +%Y%m%d-%H%M%S)-outcome"
crontab -l > "$BAK"
echo "crontab сохранён: $BAK"

if crontab -l | grep -q "outcome_report"; then
  echo "кроны outcome уже стоят — выхожу"
  exit 0
fi

( crontab -l
  echo "30 4 * * * cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.outcome_report --sync >> /var/log/parsing-seo-outcome.log 2>&1 # outcome daily sync"
  echo "10 10 * * 1 cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.outcome_report --report --tg >> /var/log/parsing-seo-outcome.log 2>&1 # outcome weekly funnel"
  echo "20 10 * * 1 cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.outcome_report --nudge --tg >> /var/log/parsing-seo-outcome.log 2>&1 # outcome weekly nudge"
) | crontab -

echo "поставлено:"
crontab -l | grep outcome_report
