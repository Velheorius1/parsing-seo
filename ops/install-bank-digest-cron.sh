#!/bin/sh
# Еженедельная банковская сводка: понедельник 07:30 UTC = 12:30 по Ташкенту.
#
# Слот выбран так, чтобы не столкнуться с понедельничными кронами, которые уже
# есть: shadow-search 03:40/03:50, source-scout 06:00-06:10, version-score 06:20,
# playbook_refine 10:00.
#
# Идемпотентно: повторный запуск не плодит дубли. Crontab бэкапится перед правкой.
set -eu

MARK="# parsing-seo-bank-digest"
LINE="30 7 * * 1 cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.bank_digest --send >> /var/log/parsing-seo-banks.log 2>&1 $MARK"

BAK="/tmp/crontab.bak-bank-digest-$(date +%Y%m%d-%H%M%S)"
crontab -l > "$BAK" 2>/dev/null || true
echo "бэкап crontab: $BAK"

if crontab -l 2>/dev/null | grep -qF "$MARK"; then
  echo "крон уже стоит — ничего не меняю:"
  crontab -l | grep -F "$MARK"
  exit 0
fi

{ crontab -l 2>/dev/null || true; echo "$LINE"; } | crontab -
echo "установлено:"
crontab -l | grep -F "$MARK"
