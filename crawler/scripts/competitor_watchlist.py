"""competitor_watchlist — кто чаще всех забирает лоты нашего профиля (список ~30 конкурентов).

Только чтение: курсор сводки, реестр и база не трогаются. Суд тот же, что у сводки
раз в 3 дня (`competitor_wins_weekly.build_report`: гейт + AI), окнами по 30 дней —
так выборка AI и потолок PostgREST остаются в тех же рамках, что у боевого разбора.
Повторы итогов ВМК-69 после смены формата id отсекает сам build_report (знали до
окна — не победа окна), поэтому окна складываются без двойного счёта.

Считается победа профиля и «спрятанная» (печатная фирма выиграла лот, который
гейт не узнал) — обе говорят, что фирма работает на нашем поле. Вне списка:
  • без 9-значного ИНН (реестр и монитор сшивают только по нему; 14-значный
    ПИНФЛ — индивидуальный предприниматель, его договоры площадки не связывают);
  • наши собственные победы (build_report кладёт их в ours, а не в items).

Usage:
  python3 -m crawler.scripts.competitor_watchlist --rank --days 120 --out /tmp/watchlist.json
"""
import os

os.environ.setdefault("PARSING_AI_LOG", "/tmp/competitor-watchlist-ai.jsonl")

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import sys  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from typing import Any, Dict, Iterable, List, Tuple  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from crawler.core import competitor_wins as CW  # noqa: E402
from crawler.core.competitor_audit import normalize_inn  # noqa: E402

logger = logging.getLogger("competitor_watchlist")

WINDOW_DAYS = 30
WINDOW_AI_CAP = 400     # на окно 30 дней: в боевом разборе окно 3 дня и потолок 60
TOP = 30


def windows(now, days, step=WINDOW_DAYS):
    # type: (datetime, int, int) -> List[Tuple[datetime, datetime]]
    """Окна [start, end) от свежего к старому, вплотную, суммарно ровно `days`."""
    end = now - CW.SAFETY_LAG
    out = []
    left = days
    while left > 0:
        span = min(step, left)
        out.append((end - timedelta(days=span), end))
        end -= timedelta(days=span)
        left -= span
    return out


def aggregate(reports):
    # type: (Iterable[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, int]]
    """Победы по ИНН из отчётов build_report. Вторым — сколько побед не вошло и почему."""
    firms = {}  # type: Dict[str, Dict[str, Any]]
    skipped = {"no_inn": 0, "pinfl": 0}
    for report in reports:
        for kind, items in (("profile", report.get("items") or []), ("hidden", report.get("printers") or [])):
            for it in items:
                w = it["win"]
                raw = w.get("winner_inn")
                inn = normalize_inn(raw)
                if inn is None:
                    skipped["pinfl" if raw and len(str(raw)) == 14 else "no_inn"] += 1
                    continue
                a = firms.setdefault(inn, {"inn": inn, "names": {}, "n": 0, "profile": 0, "hidden": 0,
                                           "sum_uzs": 0.0, "disc": [], "customers": {}, "feeds": {},
                                           "lots": []})
                name = CW.winner_name(w.get("winner"))
                a["names"][name] = a["names"].get(name, 0) + 1
                a["n"] += 1
                a[kind] += 1
                if CW.is_uzs(w.get("currency")) and w.get("won_price"):
                    a["sum_uzs"] += float(w["won_price"])
                    d = CW.discount_pct(w.get("start_price"), w.get("won_price"))
                    if d is not None:
                        a["disc"].append(d)
                cust = (w.get("customer") or "").strip()
                a["customers"][cust] = a["customers"].get(cust, 0) + 1
                a["feeds"][w.get("feed")] = a["feeds"].get(w.get("feed"), 0) + 1
                a["lots"].append({"url": w.get("source_url"), "title": (w.get("title") or "")[:90],
                                  "date": w.get("deal_date"), "won": w.get("won_price"), "kind": kind})
    return firms, skipped


def rank(firms, top=TOP):
    # type: (Dict[str, Dict[str, Any]], int) -> List[Dict[str, Any]]
    """Больше побед — выше; при равенстве — больше сумма. Имя — самое частое написание."""
    out = []
    for a in firms.values():
        row = dict(a)
        row["name"] = max(a["names"].items(), key=lambda kv: (kv[1], -len(kv[0])))[0]
        row["disc_median"] = round(CW._median(a["disc"]), 1) if a["disc"] else None
        out.append(row)
    out.sort(key=lambda r: (-r["n"], -r["sum_uzs"], r["inn"]))
    return out[:top] if top else out


async def collect(days, use_ai=True):
    # type: (int, bool) -> List[Dict[str, Any]]
    from crawler.scripts import competitor_wins_weekly as W
    W.AI_CAP = WINDOW_AI_CAP
    reports = []
    for start, end in windows(datetime.now(timezone.utc), days):
        report, _registry, undecided = await W.build_report(start, end, False, (), use_ai=use_ai)
        cov = report["coverage"]
        logger.info("окно %s—%s: профиль %d, спрятанных %d, AI %d/%d, сбоев AI %d, не решено %d, "
                    "повторов ВМК-69 %d", start.date(), end.date(), len(report["items"]),
                    len(report["printers"]), cov.get("ai_used", 0), cov.get("ai_cap", 0),
                    cov.get("ai_errors", 0), len(undecided), cov.get("civil_repeats", 0))
        if undecided:
            logger.warning("окно %s—%s: %d побед не решено — список по нему неполон",
                           start.date(), end.date(), len(undecided))
        reports.append(report)
    return reports


def render(ranked, registry_inns, skipped, days):
    # type: (List[Dict[str, Any]], Iterable[str], Dict[str, int], int) -> str
    reg = set(registry_inns)
    lines = ["Топ-%d за %d дн. (профиль + спрятанные; без ИНН %d, ПИНФЛ %d)" % (
        len(ranked), days, skipped["no_inn"], skipped["pinfl"])]
    for i, r in enumerate(ranked, 1):
        top_cust = sorted(r["customers"].items(), key=lambda kv: -kv[1])[:2]
        lines.append("%2d %s %-34s %s ×%d (проф %d, спр %d) %7.0f млн скидка %s | %s | %s" % (
            i, "R" if r["inn"] in reg else " ", r["name"][:34], r["inn"], r["n"], r["profile"], r["hidden"],
            r["sum_uzs"] / 1e6, "%s%%" % r["disc_median"] if r["disc_median"] is not None else "-",
            ", ".join(c[:24] for c, _ in top_cust), " ; ".join(x["title"][:40] for x in r["lots"][:2])))
    return "\n".join(lines)


def main():
    # type: () -> int
    ap = argparse.ArgumentParser(description="Кто чаще всех забирает лоты нашего профиля")
    ap.add_argument("--rank", action="store_true", help="посчитать и напечатать топ")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--top", type=int, default=TOP, help="0 — все фирмы")
    ap.add_argument("--no-ai", action="store_true", help="без AI (нерешённые победы не войдут)")
    ap.add_argument("--out", default="", help="JSON с полным списком (для реестра)")
    args = ap.parse_args()
    if not args.rank:
        ap.error("нужен --rank")
    if not 1 <= args.days <= 365:
        ap.error("--days: от 1 до 365")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")
    from crawler.core.competitor_audit import load_registry, registry_inns
    reports = asyncio.run(collect(args.days, use_ai=not args.no_ai))
    firms, skipped = aggregate(reports)
    ranked = rank(firms, top=0)
    print(render(ranked[:args.top] if args.top else ranked, registry_inns(load_registry()), skipped, args.days))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"days": args.days, "generated_at": datetime.now(timezone.utc).isoformat(),
                       "skipped": skipped, "firms": ranked}, fh, ensure_ascii=False, indent=1)
        logger.info("полный список (%d фирм) → %s", len(ranked), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
