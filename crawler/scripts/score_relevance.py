"""Relevance classifier scoring harness (до/после). Runs the labeled set through the
REAL notifier._ai_check_relevance (the AI relevance classifier) and reports accuracy/FP/FN.
Labeled set = human corrections from alert_feedback (message_text present) + a small
verified product-scope golden set from the 06.06 C3 work.

--playbook off : current prompt (BASELINE / до)
--playbook on  : with active classifier_playbook principles (после; needs migration 020 + active rows)

Honest: n is small (~18) → indicative, not statistically significant (TZ §5). Single-person
feedback. Use delta vs baseline on AI-achievable errors, not absolute thresholds.
"""
import argparse, asyncio, sys
sys.path.insert(0, "/opt/parsing-seo")
import httpx
from supabase import create_client
from crawler.config.settings import settings
from crawler.core.notifier import _ai_check_relevance
from crawler.core.models import RawTender

# Verified ground truth from 06.06 product-scope (Стенд/Табличка/Бейдж/Бланк/коробки=client; баннер/папка/электрика/цемент=irrelevant)
GOLDEN = [
    ("Изготовление выставочного информационного стенда из ЛДСП", "client"),
    ("Табличка информационная настольная, печать", "client"),
    ("Бейджик горизонтальный 100 штук", "client"),
    ("Бланки строгого учёта, печать 5000 шт", "client"),
    ("Поставка коробок из гофрокартона для упаковки", "client"),
    ("Услуги издательские", "client"),
    ("Каталоги и брошюры полноцветная печать", "client"),
    ("Баннер на фасад здания 3х6 м, наружная реклама", "ad"),
    ("Папка-скоросшиватель А4 канцелярская офисная", "irrelevant"),
    ("Указатель высокого напряжения УВН-10", "irrelevant"),
    ("Портландцемент ЦЕМ II А-И 32,5 Н в мешках", "irrelevant"),
    ("Поставка компьютеров и оргтехники", "irrelevant"),
]

def labeled():
    c = create_client(settings.supabase_url, settings.supabase_service_role_key)
    fb = (c.table("alert_feedback").select("corrected_label,message_text,source")
          .not_.is_("message_text", "null")
          .in_("corrected_label", ["client", "ad", "irrelevant"]).execute().data) or []
    out = [(r["message_text"], r["corrected_label"], "feedback") for r in fb if r.get("message_text")]
    out += [(t, l, "golden") for t, l in GOLDEN]
    return out

async def main(playbook):
    data = labeled()
    print("labeled set: %d (feedback %d + golden %d)" % (len(data), len(data) - len(GOLDEN), len(GOLDEN)))
    sem = asyncio.Semaphore(5)
    async with httpx.AsyncClient(timeout=25) as cl:
        async def chk(text, label, src):
            t = RawTender(id="x", external_id="x", title=text, organization="", source="Hayotbirja отбор", search_text=text)
            async with sem:
                try:
                    r = await _ai_check_relevance(t, cl)
                except Exception:
                    return (text, label, src, None)
            return (text, label, src, r)
        res = await asyncio.gather(*[chk(t, l, s) for t, l, s in data])
    tp = fp = fn = tn = failopen = 0
    lines = []
    for text, label, src, r in res:
        truth_rel = (label == "client")
        if r is None or r.score is None:
            ai_rel = True; failopen += 1; sc = "FAIL-OPEN"
        else:
            ai_rel = r.score >= 70; sc = str(r.score)
        if truth_rel and ai_rel: tp += 1
        elif truth_rel and not ai_rel: fn += 1
        elif not truth_rel and ai_rel: fp += 1
        else: tn += 1
        mark = "ok " if truth_rel == ai_rel else "MISS"
        lines.append("  %s [%s] truth=%-10s ai=%s(%s) | %s" % (mark, src[:4], label, "rel" if ai_rel else "not", sc, text[:40]))
    n = len(data); acc = (tp + tn) / n * 100 if n else 0
    print("\n".join(lines))
    print("\n=== SCORE (playbook=%s) ===" % playbook)
    print("n=%d  accuracy=%.0f%%  | TP=%d TN=%d FP(ad/irrel→rel)=%d FN(client→not)=%d  fail-open=%d" % (n, acc, tp, tn, fp, fn, failopen))
    print("(n мал → ИНДИКАТИВНО, не статзначимо; single-person feedback. Сравнивать ДЕЛЬТУ vs baseline.)")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--playbook", default="off", choices=["on", "off"])
    a = ap.parse_args()
    asyncio.run(main(a.playbook))
