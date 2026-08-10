"""first_seen_report — сколько времени до дедлайна остаётся в момент, когда лот
ВПЕРВЫЕ попал к нам, и сколько лотов мы увидели уже закрытыми.

Почему это стало возможно только сейчас. `collected_at` перезаписывается КАЖДЫМ
upsert'ом (db.py:106) — это «когда видели в последний раз», и по нему нельзя судить
ни о сроке жизни лота, ни о том, рано или поздно мы пришли. `created_at` ставится
только при вставке и больше не трогается — то есть это честное «первое появление».
Проверено 10.08 на 740 242 строках: нулей нет, у 298 369 строк created_at СТРОГО
раньше collected_at (значит поле действительно не перезаписывается). До этого в
main.md было записано обратное, и замеры такого рода считались невозможными.

Что меряем и почему именно это. Единственный показатель здоровья, который был до
сих пор, — «18-20 алертов в день». Он не отличает «пришли за неделю до дедлайна»
от «пришли за час», хотя разница между ними — это разница между участием в тендере
и его созерцанием. Здесь считается ЗАПАС = дедлайн − первое появление.

Честные оговорки, которые обязаны ехать вместе с цифрами:
  • источники с флагом deadline_is_publication_date исключены: у них в поле
    дедлайна лежит дата публикации, и «запас» там был бы фикцией;
  • дедлайн парсится продовым _parse_deadline — той же функцией, что решает
    судьбу лота в конвейере, чтобы отчёт не разошёлся с реальностью;
  • дедлайны площадок — местное время (Asia/Tashkent, UTC+5, без перехода);
  • ТОЧНОСТЬ — СУТКИ, не часы: площадки отдают дату без времени ('2026-06-16'),
    и продовый парсер время не разбирает вовсе. Дедлайн читается как КОНЕЦ дня
    («срок: 12.08» = весь день 12-го, так же понимает и продовый гейт с его
    суточной отсрочкой). Поэтому «меньше суток» здесь означает «увидели впервые
    в день дедлайна», а не «за час до»;
  • лот мог быть опубликован на площадке задолго до того, как мы его увидели —
    здесь меряется наш запас, а не скорость площадки.

Usage:
  python3 -m crawler.scripts.first_seen_report [--days 30] [--all] [--send]
    --days N   окно по первому появлению (default 30)
    --all      считать по всем собранным строкам, не только по алертам
    --send     отчёт в Telegram
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/opt/parsing-seo")

from crawler.config.settings import settings
from crawler.core.notifier import _parse_deadline

# Узбекистан: UTC+5 круглый год, перехода на летнее время нет с 1995.
_TASHKENT = timezone(timedelta(hours=5))

_BUCKETS = (
    ("уже закрыт", None, 0.0),
    ("меньше суток", 0.0, 24.0),
    ("1-3 дня", 24.0, 72.0),
    ("3-7 дней", 72.0, 168.0),
    ("больше недели", 168.0, None),
)


def publication_date_sources(config_path=None):
    # type: (str) -> set
    """Имена источников, у которых в поле дедлайна лежит дата публикации.

    Читаем сырой YAML, а не через pydantic: отчёту не нужны адаптеры, а лишний
    импорт превратил бы сбой валидации конфига в сбой измерения."""
    import yaml
    if not config_path:
        config_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "config", "sources.yaml")
    try:
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception:
        return set()
    out = set()
    for s in (raw.get("sources") or []):
        sel = s.get("html_selectors") or {}
        if sel.get("deadline_is_publication_date"):
            name = s.get("name")
            if name:
                out.add(name)
    return out


def deadline_utc(deadline_str):
    # type: (str) -> datetime
    """Строка дедлайна -> момент в UTC. None, если распарсить нечем."""
    dt = _parse_deadline(deadline_str)
    if dt is None:
        return None
    if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
        dt = dt.replace(hour=23, minute=59, second=59)  # «срок: 12.08» = весь день
    return dt.replace(tzinfo=_TASHKENT).astimezone(timezone.utc)


def hours_left(deadline_str, created_at):
    # type: (str, datetime) -> float
    """Запас в часах на момент первого появления. None — если нечего считать."""
    dl = deadline_utc(deadline_str)
    if dl is None or created_at is None:
        return None
    return (dl - created_at).total_seconds() / 3600.0


def bucket_of(hours):
    # type: (float) -> str
    for label, lo, hi in _BUCKETS:
        if lo is not None and hours < lo:
            continue
        if hi is not None and hours >= hi:
            continue
        return label
    return _BUCKETS[-1][0]


def percentile(values, p):
    # type: (list, float) -> float
    """Nearest-rank. Пустой вход -> None (а не 0: ноль здесь означал бы «в обрез»)."""
    if not values:
        return None
    xs = sorted(values)
    k = int(round((p / 100.0) * (len(xs) - 1)))
    return xs[max(0, min(k, len(xs) - 1))]


def _parse_ts(s):
    # type: (str) -> datetime
    if not s:
        return None
    txt = s.replace("Z", "+00:00")
    if "." in txt:  # postgres отдаёт микросекунды разной длины
        head, _, tail = txt.partition(".")
        frac = ""
        for ch in tail:
            if ch.isdigit():
                frac += ch
            else:
                tail = tail[len(frac):]
                break
        else:
            tail = ""
        txt = head + "." + (frac + "000000")[:6] + tail
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def summarize(rows, excluded_sources):
    # type: (list, set) -> dict
    """Чистая агрегация. rows: dicts с created_at/deadline/source/price."""
    stats = {"seen": len(rows), "excluded": 0, "unparsed": 0, "measured": 0,
             "buckets": {}, "by_source": {}, "hours": [], "late_price": 0.0,
             "unparsed_by_source": {}}
    for r in rows:
        src = r.get("source") or ""
        if src in excluded_sources:
            stats["excluded"] += 1
            continue
        h = hours_left(r.get("deadline"), _parse_ts(r.get("created_at")))
        if h is None:
            # Слепая зона: лот дошёл до человека, но СРОКА в нём нет — ни померить,
            # ни успеть. Показываем поимённо, иначе она прячется за средними.
            stats["unparsed"] += 1
            stats["unparsed_by_source"][src] = stats["unparsed_by_source"].get(src, 0) + 1
            continue
        stats["measured"] += 1
        stats["hours"].append(h)
        b = bucket_of(h)
        stats["buckets"][b] = stats["buckets"].get(b, 0) + 1
        agg = stats["by_source"].setdefault(src, {"n": 0, "late": 0, "hours": []})
        agg["n"] += 1
        agg["hours"].append(h)
        if h < 0:
            agg["late"] += 1
            try:
                stats["late_price"] += float(r.get("price") or 0)
            except (TypeError, ValueError):
                pass
    for p in (10, 25, 50, 75, 90):
        stats["p%d" % p] = percentile(stats["hours"], p)
    return stats


_MAX_PAGES = 400
_PAGE = 1000


def _fetch(days, only_alerted):
    """Строки за окно. Возвращает (rows, truncated) — усечение НЕ проглатываем:
    молча упереться в кап и отчитаться процентами по обрезку уже случалось."""
    from crawler.core.db import iter_rows
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    filters = [("gte", ("created_at", since))]
    if only_alerted:
        # not-null через сравнение: NULL не проходит ни один компаратор,
        # а filters-список умеет только одноимённые методы билдера.
        filters.append(("gte", ("alert_seq", 0)))
    rows = []
    pages = 0
    for page in iter_rows("tenders", "created_at,deadline,source,price,alert_seq,title",
                          filters=filters, page_size=_PAGE, order_col="created_at",
                          label="first_seen", max_pages=_MAX_PAGES):
        rows.extend(page)
        pages += 1
    return rows, pages >= _MAX_PAGES


def main(days, only_alerted, send):
    rows, truncated = _fetch(days, only_alerted)
    if truncated:
        print("ВНИМАНИЕ: упёрлись в лимит выгрузки (%d строк) — охват неполный, "
              "цифры ниже занижены по объёму." % (_MAX_PAGES * _PAGE))
    excluded = publication_date_sources()
    st = summarize(rows, excluded)

    scope = "алерты" if only_alerted else "все собранные строки"
    head = ("Запас до дедлайна на момент ПЕРВОГО появления (%s, %d дн)" % (scope, days))
    print(head)
    print("  строк: %d | исключено (дедлайн=дата публикации): %d | без разбора даты: %d"
          % (st["seen"], st["excluded"], st["unparsed"]))
    print("  измерено: %d (%.0f%% выборки — остальное меряем вслепую)"
          % (st["measured"], 100.0 * st["measured"] / max(1, st["seen"])))
    if st["unparsed_by_source"]:
        top = sorted(st["unparsed_by_source"].items(), key=lambda kv: -kv[1])[:8]
        print("  без срока в лоте (дошло до человека, но успеть нельзя):")
        for s, n in top:
            print("    %-40s %d" % (s[:40], n))
    if not st["measured"]:
        print("  нечего мерить")
        return 0
    print("  медиана запаса: %.1f ч (%.1f дн)" % (st["p50"], st["p50"] / 24.0))
    print("  p10 %.1f ч | p25 %.1f ч | p75 %.1f ч | p90 %.1f ч"
          % (st["p10"], st["p25"], st["p75"], st["p90"]))
    print("  распределение:")
    for label, _, _ in _BUCKETS:
        n = st["buckets"].get(label, 0)
        print("    %-14s %5d  (%4.1f%%)" % (label, n, 100.0 * n / st["measured"]))
    late = st["buckets"].get("уже закрыт", 0)
    if late:
        print("  увидели уже закрытыми: %d лотов на %.1f млрд сум"
              % (late, st["late_price"] / 1e9))
    worst = sorted(((s, a) for s, a in st["by_source"].items() if a["n"] >= 5),
                   key=lambda kv: percentile(kv[1]["hours"], 50))[:8]
    if worst:
        print("  худшие источники по медиане запаса (n>=5):")
        for s, a in worst:
            print("    %-40s n=%-4d медиана %6.1f ч  закрытых %d"
                  % (s[:40], a["n"], percentile(a["hours"], 50), a["late"]))
    if send:
        _send(head, st, late)
    return 0


def _send(head, st, late):
    import httpx
    body = ["\U0001f552 *%s*" % head,
            "измерено: %d" % st["measured"],
            "медиана запаса: *%.1f ч* (%.1f дн)" % (st["p50"], st["p50"] / 24.0),
            "p10 %.0f ч · p25 %.0f ч · p75 %.0f ч" % (st["p10"], st["p25"], st["p75"])]
    for label, _, _ in _BUCKETS:
        n = st["buckets"].get(label, 0)
        body.append("• %s — %d (%.1f%%)" % (label, n, 100.0 * n / st["measured"]))
    if late:
        body.append("\n⚠️ увидели уже закрытыми: %d на %.1f млрд сум"
                    % (late, st["late_price"] / 1e9))
    try:
        httpx.post("https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                   json={"chat_id": settings.telegram_alert_chat_id,
                         "text": "\n".join(body), "parse_mode": "Markdown"}, timeout=15)
    except Exception as exc:
        print("send failed: %s" % str(exc)[:120])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--all", action="store_true", help="все строки, не только алерты")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    sys.exit(main(a.days, not a.all, a.send))
