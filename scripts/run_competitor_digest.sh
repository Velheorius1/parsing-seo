#!/usr/bin/env bash
# Сводка по конкурентам раз в 3 дня (01.10.2026).
#
# Крон стучится КАЖДЫЙ день, а «пора ли» решает `competitor_wins_weekly --due` по
# времени последней ДОСТАВКИ (допуск 2 ч). Так не ломается на границе месяца
# (`*/3` по дням месяца даёт двухдневный разрыв 31→1) и сам догоняет пропущенный
# день: недоставленный разбор повторится на следующий день, курсор не двигается.
#
# Порядок: монитор площадок (прямые договоры UZEX; ebirja — магазин, аукцион, тендер, отбор) → сводка. Сбой
# монитора сводку не блокирует: она напишет строкой, что он не отработал.
set -uo pipefail
cd /opt/parsing-seo
PY=.venv/bin/python3
receipts=/opt/parsing-seo/data/competitor-award-monitor/receipts

$PY -m crawler.scripts.competitor_wins_weekly --due
rc=$?
if [ "$rc" -eq 10 ]; then
  exit 0                      # рано — молчим, до срока ещё не дошли
elif [ "$rc" -ne 0 ]; then
  echo "[digest] $(date -u +%FT%TZ) --due упал rc=$rc"
  exit "$rc"
fi

echo "[digest] $(date -u +%FT%TZ) монитор площадок"
scripts/run_competitor_award_weekly.sh || echo "[digest] монитор rc=$? — сводка скажет об этом строкой"

echo "[digest] $(date -u +%FT%TZ) сводка"
exec $PY -m crawler.scripts.competitor_wins_weekly --tg --monitor-receipts "$receipts"
