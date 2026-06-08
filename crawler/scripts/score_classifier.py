# -*- coding: utf-8 -*-
"""До/после harness for the Telegram message classifier.

Covers BOTH gates that decide whether a Telegram message becomes an alert:
  * GROUP path  (PR Media Group etc.): _DEMAND_PATTERNS -> _AD_FILTER ->
    _ai_extract_fields intent (demand vs ad). demand -> lead (ALERT), ad -> DROP.
  * CHANNEL path (tender channels): _ai_check_relevance product-scope -> DROP if
    score < 70.

Golden set = real messages with human labels (the 2026-06-08 false-positive
incident: #2307/#2309 self-promo ads, #2310 startup contest, #2308 real bag
lead that must NOT break, plus a multi-item "Отбор" lot that must KEEP).

Run:  cd /opt/parsing-seo && PYTHONPATH=/opt/parsing-seo .venv/bin/python3 \
        -m crawler.scripts.score_classifier
"""
import asyncio
import httpx

from crawler.config.settings import settings
from crawler.core.models import RawTender
from crawler.adapters.telegram_adapter import (
    _ai_extract_fields,
    _DEMAND_PATTERNS,
    _AD_FILTER,
)
from crawler.core.feedback import get_few_shot_examples
from crawler.core.notifier import _ai_check_relevance

# kind: "group" (demand-vs-ad) | "channel" (tender relevance)
# expect: "ALERT" | "DROP"
GOLDEN = [
    {
        "label": "#2307 self-promo (новый офсет)",
        "kind": "group", "expect": "DROP",
        "text": "bizda yana yanglik rayobi 524 gx afsetniy mashinani yo'lga "
                "qoydik kimga usluga hizmatlari kerak bo'lsa bizga murojat "
                "qilishingizni so'raymiz @ziyodilla4444 @sroj_me @oneprint2626",
    },
    {
        "label": "#2308 bag demand (HOT LEAD)",
        "kind": "group", "expect": "ALERT",
        "text": "шу сумканинг ок ранг дан керак кимда бор 1250 та кк 21*28*6 "
                "размер дан",
    },
    {
        "label": "#2309 Eco Print brand ad",
        "kind": "group", "expect": "DROP",
        "text": "eco print — shopperlar olamidagi premium sifat agar sizga "
                "shunchaki sumka emas brendingiz nufuzini ko'taradigan mahsulot "
                "kerak bo'lsa manzilda adashmadingiz barcha turdagi shopperlar "
                "premium pechat individual yondashuv",
    },
    {
        "label": "#2310 startup contest (Мин ИТ)",
        "kind": "channel", "expect": "DROP",
        "text": "siz ham ko'proq insonlar hayotini yengillashtiradigan "
                "g'oyangizni startapga aylantiring va dunyoda eng tez "
                "o'sayotgan startap ekotizimni o'sishiga hissa qo'shing",
    },
    {
        "label": "Отбор multi-item (с Книги печатные)",
        "kind": "channel", "expect": "ALERT",
        "text": "Машины швейные бытовые Стойка для велосипедов Проектор "
                "Самовар электрический Книги печатные Отбор",
    },
]


def _group_decision(text, few_shot, model):
    if not _DEMAND_PATTERNS.search(text):
        return "DROP", "no-demand"
    if _AD_FILTER.search(text):
        return "DROP", "ad-filter"
    f = _ai_extract_fields(text, settings.openrouter_api_key or "", model,
                           few_shot=few_shot)
    intent = ((f or {}).get("intent", "") or "").lower().strip()
    if intent == "ad":
        return "DROP", "ai:ad"
    if intent == "demand":
        return "ALERT", "lead (relevance-exempt)"
    return "ALERT", "intent=%s (fail-open lead)" % (intent or "none")


async def _channel_decision(text, client):
    title = text.strip().split("\n")[0][:120]
    t = RawTender(id="golden-0", external_id="0", title=title, organization="",
                  source="TG: golden", source_url="", status="active",
                  message_type="tender", search_text=text)
    res = await _ai_check_relevance(t, client)
    if res.score is not None and res.score < 70:
        return "DROP", "relevance:%s/%s" % (res.score, res.category)
    return "ALERT", "relevance:%s" % res.score


async def main():
    fast = settings.ai_relevance_model_fast or settings.ai_relevance_model
    few_shot = get_few_shot_examples(5)
    ok = 0
    async with httpx.AsyncClient(timeout=40) as client:
        for g in GOLDEN:
            if g["kind"] == "group":
                dec, why = _group_decision(g["text"], few_shot, fast)
            else:
                dec, why = await _channel_decision(g["text"], client)
            good = dec == g["expect"]
            ok += good
            print("[%s] %-34s exp=%-5s got=%-5s  %s" % (
                "OK " if good else "XXX", g["label"], g["expect"], dec, why))
    print("\nSCORE: %d/%d correct" % (ok, len(GOLDEN)))
    return 0 if ok == len(GOLDEN) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
