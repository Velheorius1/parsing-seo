"""Аудит класса «в поле deadline лежит не срок подачи» (04.08).

Зачем. Гейт `_is_deadline_expired` (grace 1 день) — единственная стадия
префильтра, которая может убить ВЕСЬ источник целиком и при этом не оставить
следа: строки собираются, ошибок нет, алертов ноль. Со стороны это выглядит
как «площадка редко публикует», и живёт годами. Так молчал Anor Bank — 37
строк, 2 алерта, при том что лоты прямо профильные.

Признак. У настоящего срока подачи заметная доля свежесобранных строк имеет
дату В БУДУЩЕМ относительно момента сбора. Если у источника таких строк нет
ни одной при вменяемой выборке — поле хранит что-то другое: дату публикации,
дату договора, срок размещения.

Оговорка, которую держим честно. `collected_at` перезаписывается при каждом
upsert (db.py:106) — это «последний раз видели», а не «первый». Для строк,
которые площадка отдаёт месяцами, это смещает оценку в сторону «просрочено».
Поэтому решающий признак не доля, а МАКСИМУМ (deadline − collected_at):
у живого поля он уверенно положительный хотя бы на одной строке.

Скрипт НЕ ставит диагноз — он даёт короткий список кандидатов на живую
проверку. Флаг `deadline_is_publication_date` ставится только после того, как
глазами увидели, что именно селектор вытаскивает (см. test_deadline_semantics).

    python3 -m crawler.scripts.audit_deadline_semantics [--days 45] [--min-rows 8]

Ноль записей. Выборка кэшируется, чтобы повторный разбор был бесплатным.
"""
import argparse
import collections
import json
import os
from datetime import date, timedelta

import httpx

from crawler.config.settings import settings
from crawler.core.notifier import _parse_deadline

CACHE = "/tmp/audit_deadline_cache.json"
_HEADERS = {"apikey": settings.supabase_service_role_key,
            "Authorization": "Bearer " + settings.supabase_service_role_key}


def _fetch_day(day_from, day_to):
    """Одни сутки постранично. Окно по суткам — иначе глубокий OFFSET ловит 57014."""
    out, offset = [], 0
    while True:
        r = httpx.get(settings.supabase_url + "/rest/v1/tenders", headers=_HEADERS, params={
            "select": "source,deadline,collected_at,alert_seq",
            "collected_at": ["gte." + day_from, "lt." + day_to],
            "order": "collected_at.desc", "offset": offset, "limit": 1000}, timeout=90)
        if r.status_code != 200:
            print("окно %s: HTTP %d — пропущено, охват неполный" % (day_from, r.status_code))
            return out
        rows = r.json()
        out.extend(rows)
        if len(rows) < 1000:
            return out
        offset += 1000
        if offset > 40000:
            print("  ! окно %s упёрлось в 40 000 строк — охват неполный" % day_from)
            return out


def load_rows(days, use_cache=True):
    if use_cache and os.path.exists(CACHE):
        with open(CACHE) as fh:
            rows = json.load(fh)
        print("выборка из кэша %s: %d строк" % (CACHE, len(rows)))
        return rows
    today = date.today()
    rows = []
    for i in range(days):
        d0 = today - timedelta(days=i + 1)
        d1 = today - timedelta(days=i)
        rows.extend(_fetch_day(d0.isoformat(), d1.isoformat()))
    with open(CACHE, "w") as fh:
        json.dump(rows, fh)
    print("выборка за %d дн: %d строк (кэш %s)" % (days, len(rows), CACHE))
    return rows


def tally(rows):
    st = collections.defaultdict(lambda: {
        "rows": 0, "with_dl": 0, "parsed": 0, "future": 0, "alerted": 0, "max_lead": None})
    for r in rows:
        s = st[r["source"]]
        s["rows"] += 1
        if r.get("alert_seq") is not None:
            s["alerted"] += 1
        dl = r.get("deadline")
        if not dl:
            continue
        s["with_dl"] += 1
        parsed = _parse_deadline(dl)
        if parsed is None:
            continue
        s["parsed"] += 1
        y, m, d = (int(x) for x in r["collected_at"][:10].split("-"))
        lead = (parsed.date() - date(y, m, d)).days
        if lead > 0:
            s["future"] += 1
        if s["max_lead"] is None or lead > s["max_lead"]:
            s["max_lead"] = lead
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--min-rows", type=int, default=8,
                    help="ниже этого числа разобранных дат вывод статистически пуст")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    st = tally(load_rows(args.days, use_cache=not args.no_cache))
    print()
    print("%-42s %7s %7s %7s %7s %8s %7s" % (
        "источник", "строк", "с дедл", "разбор", "в буд.", "макс+дн", "алерт"))
    print("-" * 92)
    suspects = []
    for src, s in sorted(st.items(), key=lambda kv: -kv[1]["parsed"]):
        if s["parsed"] < args.min_rows:
            continue
        share = s["future"] * 100.0 / s["parsed"]
        flag = ""
        if s["future"] == 0:
            flag = "  <== НИ ОДНОГО в будущем"
            suspects.append((src, s, share))
        elif share < 3:
            flag = "  <== почти нет будущих (%.1f%%)" % share
            suspects.append((src, s, share))
        print("%-42s %7d %7d %7d %7d %8s %7d%s" % (
            src[:42], s["rows"], s["with_dl"], s["parsed"], s["future"],
            s["max_lead"], s["alerted"], flag))

    print("\n" + "=" * 92)
    print("КАНДИДАТЫ на живую проверку (это НЕ диагноз — надо посмотреть, что даёт селектор):")
    if not suspects:
        print("  нет")
    for src, s, share in suspects:
        print("  %-42s разобрано %5d, в будущем %4d (%.1f%%), максимум %+d дн, алертов %d"
              % (src[:42], s["parsed"], s["future"], share, s["max_lead"], s["alerted"]))

    print("\nОтдельно: источники с ≥20 строк и НУЛЁМ алертов за окно —")
    print("тут причина может быть и не в дедлайне (ключевики, цена, мьют):")
    for src, s in sorted(st.items(), key=lambda kv: -kv[1]["rows"]):
        if s["rows"] >= 20 and s["alerted"] == 0:
            print("  %-42s строк %6d, с дедлайном %6d, в будущем %d"
                  % (src[:42], s["rows"], s["with_dl"], s["future"]))


if __name__ == "__main__":
    main()
