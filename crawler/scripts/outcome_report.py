#!/usr/bin/env python3
"""Воронка исхода: алерт -> взялись -> чем кончилось.

Из чего выросло. Разбор месяц-к-месяцу 20.08.2026: за полгода 7553 алерта на
981 млрд сум, и ни один отчёт не отвечал на вопрос «приносит ли это деньги».
Недельный скор считал recall, precision, маршрутизацию, свежесть источников —
восемь наблюдательных контуров, и все смотрят ВНУТРЬ механизма. Так и вышло,
что мы полгода спорили, 87% мусора или 43%, не зная, принёс ли хоть один
алерт хоть один заказ.

Этот отчёт отвечает ровно на три вопроса и ни на один больше:
  1. сколько показали;
  2. на сколько из них мы взялись;
  3. чем это кончилось — и в скольких случаях мы этого не знаем.

ОСТОРОЖНО С ДЕНЬГАМИ. `currency` несёт ТРИ метки на одну валюту: `UZS`, кириллическую
`Сум` (так пишет UZEX Э-магазин — 900 алертов) и мусорную `USD` (4 строки, в одной
43 млрд «долларов» — парсер взял число из текста поста). Самопроверка 20.08 поймала
на этом автора разбора: первая редакция сказала «567 млрд», молча выкинув все строки
с кириллической меткой, — недосчёт 42%. Считать по `currency IN ('UZS','Сум')`, и
помнить, что цена есть лишь у 37% алертов: любая сумма — нижняя граница.

Третья часть вопроса обязательна. Отчёт, где «неизвестно» не напечатано,
читается как «мы всё знаем», а мы не знаем: реестра результатов нет ни у
Cooperation, ни у XT-Xarid, ни у Telegram-каналов, а это ~95% алертов.

Режимы:
    --sync              сшить алерты с уже собранными фидами площадки
    --report [--tg]     воронка по месяцам
    --nudge  [--tg]     спросить исход по лотам, на которые мы подали

Ничего не выкачивает из интернета: обе стороны сшивки давно лежат в `tenders`.
"""

import argparse
import asyncio
import logging
import os
import sys
from typing import Any, Dict, List, Optional

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from crawler.config.settings import settings
from crawler.core import outcome as O

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger("outcome")

# Сколько лотов спрашиваем за раз. Больше десяти строк в одном сообщении —
# и человек не отвечает вовсе (ровно это уже случилось с дайджестом).
NUDGE_SHOWN = 8


# ── чистая логика ────────────────────────────────────────────────────────────

def month_of(ts):
    # type: (Optional[str]) -> Optional[str]
    """'2026-08-19T18:04:11+00:00' -> '2026-08'. Мусор -> None, не 'unknown':
    выдуманный месяц смешал бы строки разных периодов в одну кучу."""
    if not ts or len(str(ts)) < 7:
        return None
    s = str(ts)[:7]
    return s if (s[4] == "-" and s[:4].isdigit() and s[5:7].isdigit()) else None


def by_month(alerted_rows, outcome_rows):
    # type: (List[Dict[str, Any]], List[Dict[str, Any]]) -> List[Dict[str, Any]]
    """Свод по месяцам. Месяц берётся у АЛЕРТА, не у исхода: вопрос звучит
    «что принесли августовские алерты», а не «что закрылось в августе»."""
    seq_month = {}
    counts = {}
    for row in alerted_rows:
        m = month_of(row.get("created_at"))
        if not m:
            continue
        seq = row.get("alert_seq")
        if seq is not None:
            seq_month[int(seq)] = m
        counts.setdefault(m, []).append(row)

    grouped = {}  # type: Dict[str, List[Dict[str, Any]]]
    for row in outcome_rows:
        try:
            m = seq_month.get(int(row.get("alert_seq")))
        except (TypeError, ValueError):
            m = None
        if m:
            grouped.setdefault(m, []).append(row)

    out = []
    for m in sorted(counts):
        f = O.funnel(grouped.get(m, []))
        f["month"] = m
        f["alerted"] = len(counts[m])
        # исход неизвестен считается от ВСЕХ алертов месяца, а не от тех, по
        # которым строка исхода вообще заведена — иначе отсутствие строки
        # тихо выпадает из знаменателя и картина становится радужной
        f["result_unknown"] = f["alerted"] - (f["won_by_us"] + f["won_by_other"] + f["no_deal"])
        f["action_unknown"] = f["alerted"] - (f["bid"] + f["passed"])
        out.append(f)
    return out


def _short(name, width=42):
    # type: (str, int) -> str
    """Имя победителя для отчёта: без markdown-активных символов и обрезанное
    по слову. Слепой срез по 44 давал «... (ИНН 303018986» — незакрытая
    скобка, а незакрытая * или _ уронила бы разметку всего сообщения."""
    # Обратный апостроф у узбекских названий (FARG`ONA, G`ULOM) — часть имени,
    # но в Telegram он открывает code-span и рвёт разметку. Меняем на обычный
    # апостроф, а не выбрасываем: без него имя читается неправильно.
    clean = str(name).replace("`", "'")
    clean = "".join(ch for ch in clean if ch not in "*_[]")
    clean = " ".join(clean.split())
    if len(clean) <= width:
        return clean
    cut = clean[:width].rsplit(" ", 1)[0] or clean[:width]
    # хвост вида «… (ИНН» — открытая скобка без закрывающей; обрезаем группу
    # целиком, иначе строка читается как оборванная на полуслове
    if cut.count("(") > cut.count(")"):
        cut = cut[:cut.rindex("(")]
    return cut.rstrip(" ,(\"«") + "…"


def _pct(part, whole):
    # type: (int, int) -> str
    return "—" if not whole else "%d%%" % round(100.0 * part / whole)


def build_report_text(months, top_winners=None):
    # type: (List[Dict[str, Any]], Optional[List[tuple]]) -> str
    """Текст отчёта. Всегда печатает строку «исход неизвестен»."""
    if not months:
        return "*Воронка исхода*\n\nНет алертов за период."

    lines = ["*Воронка исхода: алерт → взялись → чем кончилось*", ""]
    lines.append("```")
    lines.append("месяц   алертов  взялись  выигр  проигр  н/розыгр  неизв")
    for m in months:
        lines.append("%-7s %7d  %7d  %5d  %6d  %8d  %5d" % (
            m["month"], m["alerted"], m["bid"], m["won_by_us"],
            m["won_by_other"], m["no_deal"], m["result_unknown"]))
    lines.append("```")

    tot_alerted = sum(m["alerted"] for m in months)
    tot_bid = sum(m["bid"] for m in months)
    tot_won = sum(m["won_by_us"] for m in months)
    tot_unknown = sum(m["result_unknown"] for m in months)

    lines.append("")
    lines.append("Всего показано: *%d*" % tot_alerted)
    lines.append("Взялись: *%d* (%s)" % (tot_bid, _pct(tot_bid, tot_alerted)))
    lines.append("Выиграли: *%d*" % tot_won)
    lines.append("Исход неизвестен: *%d* (%s)" % (tot_unknown, _pct(tot_unknown, tot_alerted)))

    if tot_bid == 0:
        lines.append("")
        lines.append("_Ни одной отметки «подал заявку». Пока её нет, отличить_")
        lines.append("_«система работает вхолостую» от «участвую мимо системы» нечем._")

    if top_winners:
        lines.append("")
        lines.append("*Кто забирал наши лоты:*")
        for name, cnt in top_winners[:5]:
            lines.append("· %s — %d" % (_short(name), cnt))
    return "\n".join(lines)


def build_nudge_keyboard(rows):
    # type: (List[Dict[str, Any]]) -> dict
    """По три кнопки на строку: выиграли / не взяли / не разыгран.

    Третья кнопка не роскошь: без неё «не взяли» пришлось бы жать и на
    отменённый лот, то есть записывать несуществующего победителя.
    """
    kb = []
    for i, row in enumerate(rows[:NUDGE_SHOWN], 1):
        seq = int(row["alert_seq"])
        kb.append([
            {"text": "%d 🏆" % i, "callback_data": "out:%d:won" % seq},
            {"text": "➖", "callback_data": "out:%d:lost" % seq},
            {"text": "🚫", "callback_data": "out:%d:dead" % seq},
        ])
    return {"inline_keyboard": kb}


def build_nudge_text(rows, titles=None):
    # type: (List[Dict[str, Any]], Optional[Dict[int, str]]) -> str
    titles = titles or {}
    lines = ["*Чем кончилось?* Отмечал «подал заявку», исход не записан.", ""]
    for i, row in enumerate(rows[:NUDGE_SHOWN], 1):
        seq = int(row["alert_seq"])
        title = (titles.get(seq) or "").strip() or "лот #%03d" % seq
        lines.append("*%d.* #%03d %s" % (i, seq, title[:70]))
    lines.append("")
    lines.append("🏆 выиграли · ➖ не взяли · 🚫 не разыгран — по номеру строки")
    return "\n".join(lines)


# ── данные ───────────────────────────────────────────────────────────────────

def _alerted_rows():
    # type: () -> List[Dict[str, Any]]
    """ВСЕ алерты, а не первая страница.

    Первый же прогон отчёта 20.08 напечатал «показано 1000» при 7553 и
    «исход неизвестен 98%»: PostgREST отдаёт максимум 1000 строк, сколько бы
    ни просил limit, и делает это молча. Занижённый знаменатель тут опаснее
    отказа — он выглядит как результат.
    """
    from crawler.core.db import _get_client
    from crawler.core.outcome import iter_by_seq
    client = _get_client()

    def build(last):
        q = (client.table("tenders")
             .select("alert_seq,created_at,title")
             .not_.is_("alert_seq", "null"))
        if last is not None:
            q = q.lt("alert_seq", last)
        return q.order("alert_seq", desc=True)

    return iter_by_seq(build)


def _top_winners(outcome_rows, limit=5):
    # type: (List[Dict[str, Any]], int) -> List[tuple]
    counts = {}  # type: Dict[str, int]
    for row in outcome_rows:
        if row.get("lot_result") == "won_by_other" and row.get("winner"):
            counts[row["winner"]] = counts.get(row["winner"], 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


async def _send(text, reply_markup=None):
    # type: (str, Optional[dict]) -> bool
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.warning("нет telegram-конфига — не отправляю")
        return False
    payload = {"chat_id": settings.telegram_alert_chat_id, "text": text,
               "parse_mode": "Markdown", "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    try:
        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning("telegram %d: %s", resp.status_code, resp.text[:250])
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("telegram упал: %s", str(exc)[:150])
        return False


def cmd_sync(dry_run=False):
    # type: (bool) -> int
    stats = O.sync_auto(dry_run=dry_run)
    logger.info("[sync] алертов со ссылкой на лот: %d", stats["alerted_with_lot"])
    logger.info("[sync] нашлась сделка: %d · не разыгран: %d · нет данных: %d",
                stats["matched_deal"], stats["matched_no_deal"], stats["unmatched"])
    logger.info("[sync] записано: %d · без изменений: %d",
                stats["written"], stats["unchanged"])
    return 0


async def cmd_report(to_tg=False):
    # type: (bool) -> int
    alerted = _alerted_rows()
    outcomes = O.load_all()
    months = by_month(alerted, outcomes)
    text = build_report_text(months, _top_winners(outcomes))
    print(text)
    if to_tg:
        ok = await _send(text)
        logger.info("отправлено: %s", ok)
    return 0


async def cmd_nudge(to_tg=False):
    # type: (bool) -> int
    rows = O.pending_confirmations(limit=NUDGE_SHOWN)
    if not rows:
        logger.info("нечего спрашивать: нет лотов с отметкой «подал» и без исхода")
        return 0
    titles = {}
    for row in _alerted_rows():
        try:
            titles[int(row["alert_seq"])] = row.get("title") or ""
        except (TypeError, ValueError, KeyError):
            pass
    text = build_nudge_text(rows, titles)
    print(text)
    if to_tg:
        ok = await _send(text, build_nudge_keyboard(rows))
        logger.info("отправлено: %s", ok)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Воронка исхода алертов")
    ap.add_argument("--sync", action="store_true", help="сшить с фидами площадки")
    ap.add_argument("--report", action="store_true", help="воронка по месяцам")
    ap.add_argument("--nudge", action="store_true", help="спросить исход по поданным")
    ap.add_argument("--tg", action="store_true", help="отправить в Telegram")
    ap.add_argument("--dry-run", action="store_true", help="только показать, не писать")
    args = ap.parse_args()

    if args.sync:
        return cmd_sync(dry_run=args.dry_run)
    if args.report:
        return asyncio.get_event_loop().run_until_complete(cmd_report(args.tg))
    if args.nudge:
        return asyncio.get_event_loop().run_until_complete(cmd_nudge(args.tg))
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
