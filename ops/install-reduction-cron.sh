#!/bin/sh
# П9: частый краул reduction-каналов (обратные аукционы hayot/xt-xarid).
# Окно приёма ставок обратного аукциона xt-xarid = 1 ЧАС после старта торгов —
# основной краул 3x/день физически опаздывает к короткоживущим лотам.
# Идемпотентно добавляет crontab-строку (раз в 20 мин, flock против самонаезда,
# lite-режим: краул+алерты, без пост-краул аналитики).
# Запуск НА VPS:  sh /opt/parsing-seo/ops/install-reduction-cron.sh
set -e
MARK="parsing-seo-reduction-lite"
LINE="*/20 * * * * flock -n /tmp/parsing-seo-reduction.lock -c 'cd /opt/parsing-seo && .venv/bin/python3 -m crawler.main --lite --sources xt-xarid-reduction hayotbirja-reduction >> /var/log/parsing-seo-reduction.log 2>&1' # ${MARK}"
if crontab -l 2>/dev/null | grep -q "${MARK}"; then
    echo "уже установлен:"; crontab -l | grep "${MARK}"
    exit 0
fi
( crontab -l 2>/dev/null; echo "${LINE}" ) | crontab -
echo "установлено:"; crontab -l | grep "${MARK}"
echo "лог: tail -f /var/log/parsing-seo-reduction.log"
