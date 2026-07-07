"""playbook_refine — distil human corrections into generalized relevance principles
(classifier_playbook). Phase 2 of TZ docs/plans/2026-06-07-classifier-playbook-loop-TZ.md.

Flow (TZ §2.2): fetch corrections -> LLM classify+distil (NO proper names) -> deterministic
signal_key -> dedup (support_count++ / insert candidate) -> promote candidate->active at
support_count>=2 (unless --bootstrap) -> report (TG --send). NEVER deletes (retire only).

Model: deepseek-v4-pro (strong, not flash — feedback_no_dumb_model_for_savings).
Usage: --days N (default 30), --bootstrap (all stay candidate), --send (TG report).
"""
import argparse, json, re, sys
sys.path.insert(0, "/opt/parsing-seo")
import httpx
from crawler.config.settings import settings
from supabase import create_client

TAXONOMY = ["relevant-rejected", "ad-as-client", "irrelevant-niche", "wrong-score", "trivial"]

# Controlled signal_slug vocabulary (E-F fix, 2026-07-06). The free-form slug made
# every correction a UNIQUE signal_key → support stuck at 1 → nothing ever promoted
# (201 clicks, 0 active principles). A closed list groups similar corrections so
# support accumulates and promotion works. Derived from Daniyar's real click themes.
SIGNAL_SLUGS = [
    "self-promo",          # реклама/самопиар своих услуг
    "vacancy",             # поиск работника/исполнителя
    "non-print-goods",     # техника/товары/мебель — не полиграфия
    "textile-sewing",      # пошив изделий (не печать логотипа)
    "cutting-outdoor",     # резка/наружка/конструкции
    "greeting-offtopic",   # приветствие/лозунг/оффтоп
    "medicine-food",       # лекарства/продукты как товар
    "construction",        # стройматериалы/строительные работы
    "services-nonprint",   # услуги вне печати (IT/СММ/ивенты/дизайн-без-печати)
    "print-rejected",      # наш полиграф-заказ ошибочно зарезан
    "packaging-rejected",  # упаковка ошибочно зарезана
    "score-borderline",    # балл на границе ниши
    "other",
]

_DISTIL_PROMPT = """Ты дистиллируешь ОДНУ человеческую коррекцию классификатора полиграф-тендеров в ОБОБЩЁННЫЙ принцип.

Коррекция:
Текст тендера: "%s"
Система решила: %s
Человек исправил на: %s

Таксономия (выбери ОДНУ): relevant-rejected (наш заказ зарезан), ad-as-client (реклама принята за заказ), irrelevant-niche (вне ниши принято за наше), wrong-score (близко но балл кривой), trivial (разовая мелочь — не возводить в принцип).

Сформулируй ПРИНЦИП — обобщённый признак ошибки, КАК ДУМАТЬ (не «что блокировать»), БЕЗ имён собственных (компаний/каналов/доменов). Конкретика — только в example.
signal_slug — ВЫБЕРИ РОВНО ОДИН из списка (не придумывай свой): self-promo, vacancy,
non-print-goods, textile-sewing, cutting-outdoor, greeting-offtopic, medicine-food,
construction, services-nonprint, print-rejected, packaging-rejected, score-borderline, other.

СТРОГО JSON: {"taxonomy":"...","principle":"...","example":"(пример: ...)","signal_slug":"..."}
Если trivial — taxonomy:"trivial", остальное пустое."""

_PROPER_NAME = re.compile(r"[A-ZА-ЯЁ][a-zа-яё]{2,}\s+[A-ZА-ЯЁ]|\.(uz|ru|com)\b|«[^»]*[A-ZА-ЯЁ]{2,}", re.U)

def _client():
    return create_client(settings.supabase_url, settings.supabase_service_role_key)

def fetch_corrections(client, days):
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    r = (client.table("alert_feedback").select("message_text,original_label,corrected_label,created_at")
         .not_.is_("message_text", "null").gte("created_at", since).execute())
    rows = r.data or []
    # only real corrections (original != corrected); skip sentinel
    return [x for x in rows if (x.get("original_label") or "") != (x.get("corrected_label") or "")
            and x.get("message_text")]

def distil(text, orig, corr):
    prompt = _DISTIL_PROMPT % (text[:300], orig, corr)
    try:
        resp = httpx.post("https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": "Bearer %s" % settings.openrouter_api_key},
            json={"model": settings.ai_relevance_model, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 600, "temperature": 0, "response_format": {"type": "json_object"}},
            timeout=40)
        if resp.status_code != 200:
            return None
        content = resp.json()["choices"][0]["message"]["content"] or ""
        m = re.search(r"\{.*\}", content, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None

def main(days, bootstrap, send):
    client = _client()
    corr = fetch_corrections(client, days)
    print("corrections in %dd: %d" % (days, len(corr)))
    inserted = updated = skipped = promoted = 0
    proposals = []
    for c in corr:
        d = distil(c["message_text"], c.get("original_label") or "?", c["corrected_label"])
        if not d or d.get("taxonomy") not in TAXONOMY:
            skipped += 1; continue
        if d["taxonomy"] == "trivial":
            skipped += 1; continue
        principle = (d.get("principle") or "").strip()
        if not principle or _PROPER_NAME.search(principle):
            # proper-name linter failed -> systemic, send to proposals not playbook
            proposals.append("[%s] %s" % (d["taxonomy"], (c["message_text"] or "")[:60]))
            skipped += 1; continue
        slug = (d.get("signal_slug") or "other").strip().lower()
        if slug not in SIGNAL_SLUGS:
            slug = "other"  # snap free-form back to the controlled vocab
        signal_key = "%s:%s" % (d["taxonomy"], slug)
        ex = (client.table("classifier_playbook").select("id,support_count,status")
              .eq("signal_key", signal_key).limit(1).execute().data)
        if ex:
            row = ex[0]; sc = (row.get("support_count") or 1) + 1
            upd = {"support_count": sc}
            if not bootstrap and sc >= 2 and row.get("status") == "candidate":
                upd["status"] = "active"; promoted += 1
            client.table("classifier_playbook").update(upd).eq("id", row["id"]).execute()
            updated += 1
        else:
            client.table("classifier_playbook").insert({
                "taxonomy": d["taxonomy"], "principle": principle,
                "example": d.get("example"), "signal_key": signal_key,
                "status": "candidate", "support_count": 1}).execute()
            inserted += 1
    summary = ("playbook_refine: corrections=%d inserted=%d updated=%d promoted=%d skipped=%d | proposals=%d"
               % (len(corr), inserted, updated, promoted, skipped, len(proposals)))
    print(summary)
    if proposals:
        print("PROMPT PROPOSALS (systemic / proper-name — not playbook):")
        for p in proposals[:10]: print("  -", p)
    if send and (inserted or promoted or proposals):
        body = "\U0001f4d8 *Playbook refine*\n" + summary
        if proposals:
            body += "\n\n*Prompt proposals* (не playbook):\n" + "\n".join("• " + p for p in proposals[:8])
        try:
            httpx.post("https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                json={"chat_id": settings.telegram_alert_chat_id, "text": body, "parse_mode": "Markdown"},
                timeout=10)
        except Exception:
            pass

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--bootstrap", action="store_true", help="all stay candidate (no auto-active)")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    main(a.days, a.bootstrap, a.send)
