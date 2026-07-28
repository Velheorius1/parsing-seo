"""Rebuild crawler/benchmark/corpus_v1.json from its sources.

The corpus is a checked-in data file, but it must not be a magic one: the first
hand-assembled version scored 42% of its entries through the WRONG gate (TG
lead texts were marked as ordinary tenders, so the relevance classifier judged
them instead of the lead spam gate they actually hit in production). This script
makes the assembly auditable and repeatable.

Sources:
  1. logs/corpus_candidates_banks.json — customer_audit --export-corpus
  2. alert_feedback                    — human-clicked labels (the strongest truth we own)
  3. real noise rows from `tenders`    — regex bait that is not our order
  4. GOLDEN from score_relevance       — the 06.06 product-scope set

Labels vs expectations (the distinction the whole score rests on):
  label            — is this a real print order in the WORLD
  expect_delivered — what policy says the pipeline should do TODAY
A cheap-but-real lot is label=relevant + expect_delivered=False, so a price
threshold change shows up as a deliberate rebaseline, never as a silent drift.

Run on the VPS (needs prod deps + DB):
  python3 -m crawler.benchmark.build_corpus > /tmp/corpus_v1.json
"""
import collections
import json
import os
import sys

sys.path.insert(0, "/opt/parsing-seo")

# Sources whose rows are customer requests: in production they carry
# message_type="customer_request" and are judged by _ai_lead_is_spam, not by
# _ai_check_relevance. Scoring them with the tender classifier measures a gate
# that never runs on them.
_LEAD_SOURCES = ("TG: PR Media Group (запросы клиентов)",)

_NOISE_TERMS = ["Изолента", "Стакан бумажный", "Портландцемент",
                "Указатель высокого", "Марля полиграфическая", "NFC визитк"]

GOLDEN_REL = [
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

MIN_PRICE_POLICY = 5_000_000


class Builder(object):
    def __init__(self):
        self.entries = []

    def add(self, **kw):
        e = {"cid": "c%04d" % (len(self.entries) + 1)}
        e.update(kw)
        e.setdefault("added", "2026-07-28")
        e.setdefault("since", "v1")
        e.setdefault("retired", None)
        e.setdefault("expect_route", None)
        self.entries.append(e)

    def snapshot(self, r, **kw):
        extra = dict((str(k), v if isinstance(v, str) else str(v))
                     for k, v in (r.get("extra_info") or {}).items() if v is not None)
        base = dict(
            kind="pipeline", external_id=r.get("external_id"), source=r.get("source"),
            title=r.get("title") or "", organization=r.get("organization") or "",
            search_text=r.get("search_text") or "", price=r.get("price"),
            currency=r.get("currency") or "UZS", deadline=r.get("deadline"),
            extra_info=extra, message_type=r.get("message_type") or "tender",
            bid_count=r.get("bid_count"), status=r.get("status") or "active",
            # Judge every dated row as of its own collection day, forever —
            # otherwise the corpus rots and "recall fell" is the calendar.
            frozen_now=str(r.get("collected_at") or "2026-07-28T00:00:00+00:00"),
        )
        base.update(kw)
        self.add(**base)


def _client():
    from crawler.core.db import _get_client
    return _get_client()


def add_bank_finds(b, path):
    if not os.path.exists(path):
        print("  (no bank export at %s — skipped)" % path, file=sys.stderr)
        return
    for r in json.load(open(path, encoding="utf-8")):
        title = (r.get("title") or "").lower()
        price = r.get("price") or 0
        is_print = any(w in title for w in
                       ("издательск", "стикер", "флаер", "закладка", "bxmlar", "печат"))
        label = "relevant" if is_print else "irrelevant"
        deliver = is_print and (not price or price >= MIN_PRICE_POLICY)
        # A deadline already past at collection time is a record, not an
        # opportunity — policy says drop regardless of topic.
        if (r.get("replay") or {}).get("dropped_at") == "deadline_expired":
            deliver = False
        org = (r.get("organization") or "").lower()
        bank = "SQB" if ("пром" in org or "sanoat" in org) else "Xalq"
        b.snapshot(r, label=label, expect_delivered=bool(deliver),
                   provenance="bank_audit:%s" % bank,
                   note="capture stage: %s" % ((r.get("replay") or {}).get("dropped_at") or "passed"))


def add_feedback(b, limit_per_label=None):
    from crawler.core.db import query_with_retry
    c = _client()

    def _q():
        return (c.table("alert_feedback")
                .select("corrected_label,message_text,source,created_at")
                .not_.is_("message_text", "null")
                .in_("corrected_label", ["client", "ad", "irrelevant"])
                .order("created_at", desc=True).limit(500).execute())

    rows = query_with_retry(_q, label="fb").data or []
    want = dict(limit_per_label or {"client": 14, "ad": 6, "irrelevant": 14})
    seen = set()
    for r in rows:
        lab = r["corrected_label"]
        txt = (r.get("message_text") or "").strip()
        if len(txt) < 25 or want.get(lab, 0) <= 0 or txt[:90] in seen:
            continue
        seen.add(txt[:90])
        want[lab] -= 1
        src = r.get("source") or ""
        is_lead = src in _LEAD_SOURCES
        b.add(kind="lead" if is_lead else "ai_only",
              external_id="fb-%04d" % (len(b.entries) + 1),
              title=txt[:200], search_text=txt[:1200], organization="",
              source=src or "Hayotbirja отбор",
              message_type="customer_request" if is_lead else "tender",
              price=None, deadline=None, extra_info={},
              label="relevant" if lab == "client" else "irrelevant",
              expect_delivered=(lab == "client"), category=lab,
              provenance="feedback:corrected",
              frozen_now="2026-07-28T00:00:00+00:00",
              note="clicked by Daniyar; %s gate in prod"
                   % ("lead-spam" if is_lead else "relevance"))


def add_noise(b):
    from crawler.core.db import query_with_retry
    c = _client()
    for term in _NOISE_TERMS:
        def _q(t=term):
            return (c.table("tenders")
                    .select("external_id,source,title,organization,search_text,price,"
                            "currency,deadline,message_type,extra_info,bid_count,status,"
                            "collected_at")
                    .ilike("title", "%%%s%%" % t).limit(2).execute())
        try:
            for r in (query_with_retry(_q, label="noise").data or []):
                b.snapshot(r, label="irrelevant", expect_delivered=False,
                           provenance="noise:known_fp",
                           note="matches a keyword but is not our order")
        except Exception as exc:
            print("  noise '%s' failed: %s" % (term, str(exc)[:60]), file=sys.stderr)


def add_golden(b):
    for text, lab in GOLDEN_REL:
        b.add(kind="ai_only", external_id="golden-%02d" % (len(b.entries) + 1),
              title=text, search_text=text, organization="",
              source="Hayotbirja отбор", message_type="tender",
              price=None, deadline=None, extra_info={},
              label="relevant" if lab == "client" else "irrelevant",
              expect_delivered=(lab == "client"), category=lab,
              provenance="golden:score_relevance",
              frozen_now="2026-06-06T00:00:00+00:00",
              note="06.06 product-scope golden")


def main():
    b = Builder()
    add_bank_finds(b, "/opt/parsing-seo/logs/corpus_candidates_banks.json")
    add_noise(b)
    add_golden(b)
    add_feedback(b)

    corpus = {
        "corpus_version": "v1.1",
        "created": "2026-07-28",
        "notes": ("Замороженный бенчмарк ВЕРСИИ краулера. Не дублирует weekly_metrics: "
                  "тот меряет живую неделю (данные плавают, версии несравнимы), этот — "
                  "детерминированный прогон одного набора через любой коммит. "
                  "label = правда о мире; expect_delivered = что политика предписывает "
                  "сегодня. kind=lead судится spam-гейтом, как в проде. "
                  "Пересобрать: python3 -m crawler.benchmark.build_corpus"),
        "entries": b.entries,
    }
    print(json.dumps(corpus, ensure_ascii=False, indent=1))
    st = collections.Counter(e["kind"] for e in b.entries)
    print("entries=%d kinds=%s deliver=%s" % (
        len(b.entries), dict(st),
        dict(collections.Counter(e["expect_delivered"] for e in b.entries))),
        file=sys.stderr)


if __name__ == "__main__":
    main()
