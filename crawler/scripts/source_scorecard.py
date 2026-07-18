"""source_scorecard — per-source health digest + demote/promote proposals (C, 2026-07-16).

Nyro "budget by activity": each source earns its keep. Surfaces, per source over 7d:
alerts, lead count, feedback miss-rate (from alert_feedback), latest quality_score, and
freshness — then proposes DEMOTE (noisy, no value) / PRODUCTIVE (delivers leads). Crawl
frequency changes stay Daniyar's manual call — this only proposes.

Also renders the weekly "learned" line (classifier_playbook active + promoted-this-week)
so the feedback loop shows a visible effect and doesn't die (proven failure mode).

Data already on the VPS (source_quality_metrics + tenders + alert_feedback + classifier_playbook);
aggregation is done in Python via the service_role client (portable, no Management API).

Cron (host, weekly): 15 6 * * 1 cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.source_scorecard --tg
Usage: python3 -m crawler.scripts.source_scorecard [--tg] [--days 7]
"""
import argparse
from datetime import datetime, timedelta, timezone

import httpx

from crawler.config.settings import settings

# Thresholds for proposals (report-only).
_DEMOTE_MISS_RATE = 0.8   # >=80% of clicks are "мимо"
_DEMOTE_MIN_FB = 3        # with at least this many feedback clicks (enough signal)
_PRODUCTIVE_HITS = 2      # >=2 "client" hits → earns its keep


def _verdict(r):
    # type: (dict) -> str
    """Classify one source's 7d card. demote = noisy with zero value; productive = delivers
    leads; unrated = lots of alerts but no clicks to judge on; else ok."""
    miss_rate = (r["miss"] / r["fb"]) if r["fb"] else None
    if r["fb"] >= _DEMOTE_MIN_FB and miss_rate is not None and miss_rate >= _DEMOTE_MISS_RATE and r["hit"] == 0:
        return "demote"
    if r["hit"] >= _PRODUCTIVE_HITS:
        return "productive"
    if r["alerts"] >= 10 and r["fb"] == 0:
        return "unrated"
    return "ok"


def _client():
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def _fetch_all(make_query, page=1000):
    """Paginate a query. make_query() must return a FRESH builder each call (reusing one
    builder across .range() pages is unsafe — mirrors migrate_legacy_urls.py)."""
    rows, off = [], 0
    while True:
        d = make_query().range(off, off + page - 1).execute().data or []
        rows.extend(d)
        if len(d) < page:
            return rows
        off += page


def build_scorecard(client, days):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    agg = {}

    def row(src):
        return agg.setdefault(src, {"alerts": 0, "leads": 0, "fb": 0, "miss": 0, "hit": 0, "qscore": None})

    # alerts + leads from tenders
    for t in _fetch_all(lambda: client.table("tenders").select("source,message_type,alert_seq")
                        .not_.is_("alert_seq", "null").gte("created_at", since)):
        r = row(t.get("source") or "?")
        r["alerts"] += 1
        if t.get("message_type") == "customer_request":
            r["leads"] += 1
    # feedback miss/hit from alert_feedback
    for f in _fetch_all(lambda: client.table("alert_feedback").select("source,corrected_label").gte("created_at", since)):
        r = row(f.get("source") or "?")
        r["fb"] += 1
        if f.get("corrected_label") in ("ad", "irrelevant"):
            r["miss"] += 1
        elif f.get("corrected_label") == "client":
            r["hit"] += 1
    # latest quality_score per source
    qs = _fetch_all(lambda: client.table("source_quality_metrics").select("source,metric_value,computed_at")
                    .eq("metric_type", "quality_score").gte("computed_at", since).order("computed_at"))
    for m in qs:  # ordered asc → last write per source wins
        if m.get("source") in agg:
            agg[m["source"]]["qscore"] = m.get("metric_value")

    cards = []
    for src, r in agg.items():
        miss_rate = (r["miss"] / r["fb"]) if r["fb"] else None
        cards.append(dict(source=src, miss_rate=miss_rate, verdict=_verdict(r), **r))
    cards.sort(key=lambda c: -c["alerts"])
    return cards


def learned_line(client, days):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        active = client.table("classifier_playbook").select("signal_key,support_count").eq("status", "active").order("support_count", desc=True).execute().data or []
        promoted = client.table("classifier_playbook").select("id", count="exact").eq("status", "active").gte("updated_at", since).execute()
        n_new = promoted.count or 0
        top = ", ".join(a["signal_key"].split(":")[-1] for a in active[:3])
        return "🧠 Выучено: playbook %d активных принципов (+%d за %dд). Топ: %s" % (len(active), n_new, days, top or "—")
    except Exception as exc:
        return "🧠 Выучено: playbook — (ошибка чтения: %s)" % str(exc)[:60]


def _fmt(cards, learned):
    demote = [c for c in cards if c["verdict"] == "demote"]
    prod = [c for c in cards if c["verdict"] == "productive"]
    unrated = [c for c in cards if c["verdict"] == "unrated"]
    lines = ["📊 *Скоркарта источников (7д)*", ""]
    lines.append("```")
    lines.append("%-34s %5s %5s %4s %4s" % ("источник", "алрт", "мимо%", "хит", "qsc"))
    for c in cards[:14]:
        mr = "%.0f" % (c["miss_rate"] * 100) if c["miss_rate"] is not None else "-"
        qs = "%.0f" % c["qscore"] if c["qscore"] is not None else "-"
        lines.append("%-34s %5d %5s %4d %4s" % (c["source"][:34], c["alerts"], mr, c["hit"], qs))
    lines.append("```")
    if demote:
        lines.append("\n🔻 *Демоут-кандидаты* (шум, 0 пользы — реже крол / приглушить, твой выбор):")
        for c in demote:
            lines.append("• %s — %d алертов, %.0f%% мимо (%d/%d), 0 хитов" % (
                c["source"], c["alerts"], c["miss_rate"] * 100, c["miss"], c["fb"]))
    if prod:
        lines.append("\n🔺 *Продуктивные* (дают лиды/клиентов):")
        for c in sorted(prod, key=lambda x: -x["hit"]):
            lines.append("• %s — %d хитов из %d меток" % (c["source"], c["hit"], c["fb"]))
    if unrated:
        lines.append("\n❔ *Без оценки* (много алертов, 0 меток — прокликай для настройки): %s"
                     % ", ".join(c["source"] for c in unrated[:5]))
    lines.append("\n" + learned)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tg", action="store_true", help="send report to Telegram")
    ap.add_argument("--days", type=int, default=7)
    a = ap.parse_args()
    client = _client()
    cards = build_scorecard(client, a.days)
    body = _fmt(cards, learned_line(client, a.days))
    print(body)
    if a.tg:
        try:
            httpx.post("https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                       json={"chat_id": settings.telegram_alert_chat_id, "text": body, "parse_mode": "Markdown"},
                       timeout=10)
            print("\n[scorecard] sent to TG")
        except Exception as exc:
            print("\n[scorecard] TG send failed: %s" % str(exc)[:80])


if __name__ == "__main__":
    main()
