#!/bin/sh
# Версионный бенчмарк: ежедневный тик коммита + недельный балл 0-10.
#
# Зачем: за июль пайплайн менялся десятки раз, и ни одна правка не сверялась с
# эталоном — регрессия recall'а ловилась случайно. Тик пишет, какой коммит стоит
# на проде (чтобы позже связать просадку балла с деплоем, а не гадать), недельный
# прогон гоняет замороженный корпус через текущий код и шлёт балл в Telegram.
#
# Стоимость: тик бесплатный; --score ≈ $0.02-0.17 за прогон (75 записей через
# гибрид flash→pro), раз в неделю — меньше доллара в месяц.
#
# Идемпотентно. Запуск НА VPS:  sh /opt/parsing-seo/ops/install-version-cron.sh
set -e
MARK_V="parsing-seo-version-tick"
MARK_S="parsing-seo-version-score"
LOG="/var/log/parsing-seo-version.log"

LINE_V="15 6 * * * cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.version_scorecard --log-version >> ${LOG} 2>&1 # ${MARK_V}"
# Понедельник 06:20 — после source_scout (06:00/06:05/06:10) и до scorecard 06:15
# уже занят, поэтому 06:20: отчёты не сталкиваются в одну минуту.
LINE_S="20 6 * * 1 cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.version_scorecard --score --tg >> ${LOG} 2>&1 # ${MARK_S}"

BACKUP="/root/crontab.bak-$(date +%Y%m%d-%H%M)-version"
crontab -l > "${BACKUP}" 2>/dev/null || true
echo "бэкап crontab: ${BACKUP}"

added=0
if crontab -l 2>/dev/null | grep -q "${MARK_V}"; then
    echo "тик уже стоит"
else
    ( crontab -l 2>/dev/null; echo "${LINE_V}" ) | crontab -
    added=1
fi
if crontab -l 2>/dev/null | grep -q "${MARK_S}"; then
    echo "недельный балл уже стоит"
else
    ( crontab -l 2>/dev/null; echo "${LINE_S}" ) | crontab -
    added=1
fi

echo "текущее:"
crontab -l | grep -E "${MARK_V}|${MARK_S}" || echo "  (ничего не найдено — проверь вручную)"
[ "${added}" -eq 1 ] && echo "лог: tail -f ${LOG}"
echo "история баллов: /opt/parsing-seo/logs/version_scores.jsonl"
exit 0
