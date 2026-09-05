"""Витрина здоровья источников: одна команда вместо пяти сторожей.

    python3 -m crawler.scripts.source_registry            # таблица в stdout
    python3 -m crawler.scripts.source_registry --tg       # сводка в Telegram
    python3 -m crawler.scripts.source_registry --json     # машинный вывод
    python3 -m crawler.scripts.source_registry --problems # только проблемные

ЗАЧЕМ. Про здоровье источника знали пять сторожей и каждый по-своему; списков
исключений было три. Простой прокси 29.08-04.09 показал цену: сигналы шли из
четырёх мест, и ни один не сказал «встали два источника, это 43% потока» —
сложить картину было негде. Скрипт ничего не решает и никуда не пишет: он
показывает то, что реестр уже знает.
"""
import argparse
import json
import logging
import os
import sys

import httpx

from crawler.config.settings import settings
from crawler.core.source_health import (
    VERDICT_HEAVY_STALE, VERDICT_NEVER, VERDICT_OK, VERDICT_SILENT,
    VERDICT_SILENT_EXPECTED, build_registry,
)

logger = logging.getLogger(__name__)

TG_LIMIT = 4096
_CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "config", "sources.yaml")

_ICON = {
    VERDICT_OK: "✅",
    VERDICT_HEAVY_STALE: "\U0001f7e5",
    VERDICT_SILENT: "⚠️",
    VERDICT_SILENT_EXPECTED: "\U0001f507",
    VERDICT_NEVER: "⬜",
}


def _age(rec):
    # type: (dict) -> str
    h = rec.get("silent_hours")
    if h is None:
        return "нет данных"
    if h < 48:
        return "%dч" % int(h)
    return "%dд" % int(h // 24)


def render_table(reg):
    # type: (dict) -> str
    lines = ["%-4s %-40s %7s %9s %8s %s"
             % ("", "источник", "доля", "молчит", "порог", "вердикт")]
    for r in reg["sources"]:
        thr = r.get("threshold_hours")
        lines.append("%-4s %-40s %6.1f%% %9s %8s %s" % (
            _ICON.get(r["verdict"], "?"), r["name"][:40], r["share_pct"], _age(r),
            ("%dч" % thr) if thr else "—", r["verdict"]))
    lines.append("")
    lines.append("алертов за 30 дней: %d (без заглушённых источников)" % reg["alerts_total"])
    return "\n".join(lines)


def render_digest(reg, limit=TG_LIMIT):
    # type: (dict, int) -> str
    """Короткая сводка: сначала поломки, потом молчуны, решения — числом."""
    src = reg["sources"]
    broken = [r for r in src if r["verdict"] == VERDICT_HEAVY_STALE]
    silent = [r for r in src if r["verdict"] == VERDICT_SILENT]
    never = [r for r in src if r["verdict"] == VERDICT_NEVER]
    expected = [r for r in src if r["verdict"] == VERDICT_SILENT_EXPECTED]
    ok = [r for r in src if r["verdict"] == VERDICT_OK]

    lines = ["\U0001fa7a Здоровье источников", ""]
    if broken:
        share = sum(r["share_pct"] for r in broken)
        lines.append("Поломки (%d), суммарно %.0f%% потока:" % (len(broken), share))
        lines.extend("· %s — молчит %s, %.1f%% алертов"
                     % (r["name"], _age(r), r["share_pct"]) for r in broken)
        lines.append("")
    if silent:
        lines.append("Молчат без объяснения (%d):" % len(silent))
        lines.extend("· %s — %s" % (r["name"], _age(r)) for r in silent[:10])
        if len(silent) > 10:
            lines.append("· … и ещё %d" % (len(silent) - 10))
        lines.append("")
    if never:
        lines.append("Ни одной строки за историю: %d" % len(never))
    lines.append("Молчат по договорённости: %d" % len(expected))
    lines.append("Свежие: %d" % len(ok))
    lines.append("")
    lines.append("Алертов за 30 дней: %d. Это витрина, а не тревога:"
                 " поломку присылает healthcheck." % reg["alerts_total"])
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def send_telegram(text):
    # type: (str) -> bool
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.warning("нет телеграм-кредов — отправка пропущена")
        return False
    url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    try:
        resp = httpx.post(url, json={"chat_id": settings.telegram_alert_chat_id,
                                     "text": text, "disable_notification": True},
                          timeout=15)
        return 200 <= resp.status_code < 300
    except Exception as exc:
        logger.warning("отправка не удалась: %s", str(exc)[:100])
        return False


def main():
    parser = argparse.ArgumentParser(description="Витрина здоровья источников")
    parser.add_argument("--tg", action="store_true", help="отправить сводку в Telegram")
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    parser.add_argument("--problems", action="store_true", help="только проблемные строки")
    parser.add_argument("--days", type=int, default=30, help="окно для весов")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    reg = build_registry(_CONFIG, days=args.days)
    if args.problems:
        keep = (VERDICT_HEAVY_STALE, VERDICT_SILENT, VERDICT_NEVER)
        reg = dict(reg, sources=[r for r in reg["sources"] if r["verdict"] in keep])

    if args.json:
        print(json.dumps(reg, ensure_ascii=False, indent=2))
    else:
        print(render_table(reg))

    if args.tg:
        ok = send_telegram(render_digest(reg))
        print("[TG] %s" % ("отправлено" if ok else "НЕ отправлено"))
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
