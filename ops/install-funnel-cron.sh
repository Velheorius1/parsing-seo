#!/bin/sh
# Сторож воронки + второй шанс — два крона, закрывающие июльский провал.
#
# Зачем. Поток алертов упал 84 → 14 в день за пять недель, и заметил это человек
# вручную через месяц: между «сбор исправен» (freshness_watchdog,
# zero_result_tracker) и «алерты приходят» не следил никто. Отдельно от этого
# промах при первом появлении лота был вечен — upsert отдаёт в алерты только
# впервые увиденные строки, поэтому добавленный ключ не возвращает уже
# собранный лот (кейс Xalq Bank, 1 225 574 400 сум).
#
# Время выбрано в свободные минуты: 06:00-06:20 заняты source_scout и
# version_scorecard, 07:00 — freshness_watchdog.
#   06:40 — сторож воронки (только чтение, шлёт в TG при смене набора тревог)
#   06:50 — второй шанс (досылает через send_alerts; flock, чтобы прогоны не
#           наложились, если выборка затянется)
#
# Стоимость: сторож бесплатный; второй шанс ≈ 40 вызовов быстрой модели в день.
#
# Идемпотентно. Запуск НА VPS:  sh /opt/parsing-seo/ops/install-funnel-cron.sh
set -e
MARK_W="parsing-seo-funnel-watchdog"
MARK_R="parsing-seo-recheck"
LOG="/var/log/parsing-seo-funnel.log"

LINE_W="40 6 * * * cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.funnel_watchdog >> ${LOG} 2>&1 # ${MARK_W}"
LINE_R="50 6 * * * flock -n /tmp/parsing-seo-recheck.lock -c 'cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.recheck --execute >> ${LOG} 2>&1' # ${MARK_R}"

BACKUP="/root/crontab.bak-$(date +%Y%m%d-%H%M)-funnel"
crontab -l > "${BACKUP}" 2>/dev/null || true
echo "бэкап crontab: ${BACKUP}"

added=0
if crontab -l 2>/dev/null | grep -q "${MARK_W}"; then
    echo "сторож воронки уже стоит"
else
    ( crontab -l 2>/dev/null; echo "${LINE_W}" ) | crontab -
    added=1
fi
if crontab -l 2>/dev/null | grep -q "${MARK_R}"; then
    echo "второй шанс уже стоит"
else
    ( crontab -l 2>/dev/null; echo "${LINE_R}" ) | crontab -
    added=1
fi

if [ "${added}" = "1" ]; then
    echo "установлено. проверка:"
    crontab -l | grep -E "${MARK_W}|${MARK_R}"
else
    echo "оба крона уже были на месте — ничего не менял"
fi
echo
echo "снять:  crontab -l | grep -v '${MARK_W}' | grep -v '${MARK_R}' | crontab -"
echo "лог:    tail -f ${LOG}"
