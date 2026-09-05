#!/bin/bash
# МЕСТО ЖИТЕЛЬСТВА: /root/coop-online-check.sh на VPS (НЕ в /opt/parsing-seo —
# любой файл в дереве репозитория ломает `git pull --ff-only` авто-деплоя).
# Здесь копия, чтобы скрипт не потерялся: cron ссылается на путь в /root.
# Установка: scp ops/coop-online-check.sh root@VPS:/root/ && chmod 700.
# Крон: 30 6 * * 1 /root/coop-online-check.sh >/dev/null 2>&1
# Доступность cooperation.uz тем же путём, которым мы её реально собираем.
#
# ЧТО БЫЛО НЕ ТАК (разбор 05.09).
# 1. В crontab проверка стояла одной строкой, и токен бота подставлялся прямо в
#    командную строку curl: виден в ps любому процессу, попадает в лог cron.
# 2. Проверка ходила на площадку НАПРЯМУЮ с VPS. Cooperation.uz блокирует
#    датацентровые адреса — ровно поэтому сбор идёт через резидентный прокси.
#    Первый curl падал всегда, скрипт выходил по `|| exit 0` молча, и
#    понедельничное «cooperation.uz ONLINE» не приходило никогда. Крон выглядел
#    живым и не проверял ничего.
# 3. Вывод гасился в /dev/null, поэтому пункт 2 нельзя было заметить.
#
# ЧТО СЕЙЧАС. Ходим через тот же резидентный прокси, что и fetch_cooperation.
# Пишем результат в лог. В Telegram сообщаем только СМЕНУ состояния: «снова
# отвечает» или «перестала отвечать». Еженедельное «всё хорошо» — это шум,
# которого в канале и так было слишком много.
set -uo pipefail
ENV_FILE="/opt/parsing-seo/.env"
LOG="/var/log/coop-online-check.log"
STATE="/var/lib/coop-online.state"
URL="https://new.cooperation.uz/"

read_env() { sed -n "s/^$1=//p" "$ENV_FILE" | head -1; }

PROXY=$(read_env RESIDENTIAL_PROXY_URL)
if [ -n "$PROXY" ]; then
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 -x "$PROXY" "$URL" || echo 000)
  VIA="прокси"
else
  CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$URL" || echo 000)
  VIA="напрямую (прокси не настроен)"
fi

NOW="ok"
[ "$CODE" = "200" ] || NOW="down"
PREV=$(cat "$STATE" 2>/dev/null || echo "unknown")
echo "$(date -u +%FT%TZ) $VIA HTTP $CODE ($PREV -> $NOW)" >> "$LOG"
mkdir -p "$(dirname "$STATE")" 2>/dev/null || true
echo "$NOW" > "$STATE"

[ "$NOW" = "$PREV" ] && exit 0          # состояние не менялось — молчим
[ "$PREV" = "unknown" ] && exit 0       # первый запуск не повод будить

TOKEN=$(read_env TELEGRAM_BOT_TOKEN)
CHAT=$(read_env TELEGRAM_ALERT_CHAT_ID)
if [ -z "$TOKEN" ] || [ -z "$CHAT" ]; then
  echo "$(date -u +%FT%TZ) нет кредов в $ENV_FILE" >> "$LOG"
  exit 1
fi
if [ "$NOW" = "ok" ]; then
  MSG="cooperation.uz снова отвечает (через $VIA, HTTP $CODE)"
else
  MSG="cooperation.uz не отвечает: HTTP $CODE через $VIA. Сбор лотов встанет — за ним 26% алертов."
fi

# Токен уходит в curl через stdin-конфиг: в argv он был бы виден в ps.
TG=$(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\ndata-urlencode = "chat_id=%s"\ndata-urlencode = "text=%s"\nsilent\noutput = "/dev/null"\nwrite-out = "%%{http_code}"\n' \
  "$TOKEN" "$CHAT" "$MSG" | curl --config -)
echo "$(date -u +%FT%TZ) telegram HTTP $TG" >> "$LOG"
[ "$TG" = "200" ] || exit 1
