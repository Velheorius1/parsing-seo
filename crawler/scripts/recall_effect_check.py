"""recall_effect_check — did pinning the recall principles change the classifier?

Context (2026-07-25): the playbook was 23/23 rejection-side, so the prompt could only
push the classifier toward rejecting. Two recall principles were promoted AND pinned
into the prompt (feedback._RECALL_TAXONOMY, commit 73a23ca):
  - «Издательские и типографские услуги — профильный заказ, даже если сформулировано
     общей фразой "услуга"»
  - «Приоритет содержанию (конкретные параметры), а не форме — вопрос ≠ не заказ»

This compares the window after the change against the window before and reports to
Telegram. Deliberately conservative: it reports numbers and sample sizes and refuses
to call a winner on thin data — a recall guard that "worked" on n=12 is noise.

Baseline captured at cutover (2026-07-25 14:00Z):
  AI reject-rate among items reaching the AI gate: 68% before, 68% after (2h window)
  Alerts/day: 51, 50, 58, 72, 73 (07-20..07-24)

Usage: python3 -m crawler.scripts.recall_effect_check [--tg] [--cutover ISO]
"""
import argparse
import sys

sys.path.insert(0, "/opt/parsing-seo")

CUTOVER = "2026-07-25T14:00:00Z"
BEFORE_FROM = "2026-07-18T00:00:00Z"
MIN_SAMPLE = 60  # below this the after-window can't distinguish signal from noise


def _client():
    from crawler.core.db import _get_client
    return _get_client()


def _fetch_scored(client, since, until=None):
    """Rows that reached the AI gate (relevance_score persisted) in a window."""
    from crawler.core.db import query_with_retry
    rows = []
    offset = 0
    while True:
        def _q(o=offset):
            q = (client.table("tenders")
                 .select("title,relevance_score,relevance_category,alert_seq,telegram_message_id,collected_at")
                 .not_.is_("relevance_score", "null")
                 .gte("collected_at", since))
            if until:
                q = q.lt("collected_at", until)
            return q.range(o, o + 999).execute()
        resp = query_with_retry(_q, label="recall-check p%d" % offset)
        page = resp.data or []
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
        if offset > 20000:
            break
    return rows


def _stats(rows):
    n = len(rows)
    if not n:
        return {"n": 0, "reject_pct": None, "avg": None, "pub_n": 0, "pub_reject_pct": None}
    rejected = [r for r in rows if (r.get("relevance_score") or 0) < 50]
    scores = [r.get("relevance_score") or 0 for r in rows]
    # the sub-population the recall guards speak to
    pub = [r for r in rows if any(k in (r.get("title") or "").lower()
                                  for k in ("издательск", "типографск", "печат", "распечат"))]
    pub_rej = [r for r in pub if (r.get("relevance_score") or 0) < 50]
    return {
        "n": n,
        "reject_pct": round(100.0 * len(rejected) / n),
        "avg": round(sum(scores) / float(n)),
        "pub_n": len(pub),
        "pub_reject_pct": round(100.0 * len(pub_rej) / len(pub)) if pub else None,
    }


def _playbook_state(client):
    """Regression guard: are the recall principles still active AND still in the prompt?"""
    from crawler.core.feedback import get_relevance_playbook, _RECALL_TAXONOMY
    active = (client.table("classifier_playbook").select("signal_key,taxonomy")
              .eq("status", "active").execute().data or [])
    recall_active = [r for r in active if (r.get("taxonomy") or "") == _RECALL_TAXONOMY]
    pb = get_relevance_playbook()
    in_prompt = sum(1 for line in pb.split("\n") if ("[%s]" % _RECALL_TAXONOMY) in line)
    return len(active), len(recall_active), in_prompt


def main(send, cutover):
    client = _client()
    before = _stats(_fetch_scored(client, BEFORE_FROM, cutover))
    after = _stats(_fetch_scored(client, cutover))
    n_active, n_recall, n_prompt = _playbook_state(client)

    lines = ["\U0001f9ea *Эффект recall-принципов* (срез %s)" % cutover[:16].replace("T", " ")]
    lines.append("")
    lines.append("```")
    lines.append("%-22s %8s %8s" % ("", "ДО", "ПОСЛЕ"))
    lines.append("%-22s %8s %8s" % ("дошло до AI", before["n"], after["n"]))
    lines.append("%-22s %7s%% %7s%%" % ("отклонено AI", before["reject_pct"], after["reject_pct"]))
    lines.append("%-22s %8s %8s" % ("средний score", before["avg"], after["avg"]))
    lines.append("%-22s %8s %8s" % ("печать/издат: шт", before["pub_n"], after["pub_n"]))
    lines.append("%-22s %7s%% %7s%%" % ("печать/издат: реджект", before["pub_reject_pct"], after["pub_reject_pct"]))
    lines.append("```")

    # Verdict — refuse to over-read thin data
    if after["n"] < MIN_SAMPLE:
        lines.append("⚠️ Выборка «после» мала (%d < %d) — вывод делать рано, "
                     "нужен ещё день." % (after["n"], MIN_SAMPLE))
    else:
        d = after["reject_pct"] - before["reject_pct"]
        if d <= -5:
            lines.append("✅ Реджект-рейт упал на %d п.п. — recall-guard'ы работают." % abs(d))
        elif d >= 5:
            lines.append("\U0001f7e5 Реджект-рейт ВЫРОС на %d п.п. — не то, чего ждали, разобраться." % d)
        else:
            lines.append("➡️ Реджект-рейт почти не изменился (%+d п.п.). "
                         "Значит принципы влияют лишь на пограничные кейсы, а не на общий поток." % d)

    lines.append("")
    lines.append("Playbook: %d активных, из них recall %d; в промпте recall-строк: %d"
                 % (n_active, n_recall, n_prompt))
    if n_prompt < n_recall:
        lines.append("\U0001f7e5 РЕГРЕССИЯ: не все recall-принципы доходят до промпта — "
                     "проверить get_relevance_playbook (пиннинг 73a23ca).")

    text = "\n".join(lines)
    print(text)

    if send:
        import httpx
        from crawler.config.settings import settings
        try:
            with httpx.Client(timeout=15, trust_env=False) as c:
                r = c.post("https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                           json={"chat_id": settings.telegram_alert_chat_id, "text": text,
                                 "parse_mode": "Markdown", "disable_web_page_preview": True})
            print("[TG]", r.status_code)
        except Exception as exc:
            print("[TG] failed:", str(exc)[:120])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tg", action="store_true", help="send the report to Telegram")
    ap.add_argument("--cutover", default=CUTOVER)
    a = ap.parse_args()
    main(a.tg, a.cutover)
