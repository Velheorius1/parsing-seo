"""AI enrichment — fill missing fields (price, deadline, organization) for all sources.

Uses Qwen via OpenRouter. Only enriches tenders where critical fields are missing.
Cost: ~$0.003/day at 500 tenders (Qwen3-30B-A3B at $0.10/M input).
"""

import asyncio
import json
import logging
import re
from typing import List, Optional

import httpx

from crawler.config.settings import settings
from crawler.core.models import RawTender

logger = logging.getLogger(__name__)

# Max concurrent AI calls to OpenRouter
MAX_CONCURRENT = 10

_ENRICH_PROMPT = """Извлеки из текста тендера/закупки:
- organization: заказчик или продавец (название)
- price: сумма (только число, без валюты)
- currency: UZS/USD/EUR
- deadline: дата окончания (DD.MM.YYYY)

Текст: {text}

Ответь СТРОГО в JSON:
{{"organization": "...", "price": null, "currency": "UZS", "deadline": null}}

Если поле не найдено, ставь null. Только JSON, без пояснений.
/no_think"""


async def _enrich_one(tender, client, semaphore):
    # type: (RawTender, httpx.AsyncClient, asyncio.Semaphore) -> bool
    """Enrich one tender with AI. Returns True if any field was filled."""
    if not settings.openrouter_api_key:
        return False

    # Build text from available fields
    text_parts = [tender.title]
    if tender.search_text:
        text_parts.append(tender.search_text[:500])
    text = " | ".join(text_parts)[:700]

    prompt = _ENRICH_PROMPT.format(text=text)

    async with semaphore:
        try:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": "Bearer %s" % settings.openrouter_api_key},
                json={
                    "model": settings.ai_relevance_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 100,
                    "temperature": 0,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                return False

            raw = resp.json()["choices"][0]["message"]["content"] or ""
            # Strip thinking tags
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

            # Extract JSON from response (greedy match to handle nested braces)
            json_match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not json_match:
                return False

            try:
                data = json.loads(json_match.group())
            except json.JSONDecodeError:
                # Fallback: try non-greedy if greedy captured too much
                json_match2 = re.search(r"\{[^}]+\}", raw)
                if not json_match2:
                    return False
                data = json.loads(json_match2.group())
            filled = False

            if not tender.organization and data.get("organization"):
                tender.organization = str(data["organization"])[:200]
                filled = True

            if tender.price is None and data.get("price") is not None:
                try:
                    price_val = data["price"]
                    if isinstance(price_val, str):
                        price_val = price_val.replace(" ", "").replace(",", ".")
                    tender.price = float(price_val)
                    filled = True
                except (ValueError, TypeError):
                    pass

            if data.get("currency") and data["currency"] in ("USD", "EUR"):
                tender.currency = data["currency"]

            if not tender.deadline and data.get("deadline"):
                tender.deadline = str(data["deadline"])
                filled = True

            return filled

        except Exception as exc:
            logger.debug("[Enricher] Error enriching tender %s: %s", tender.id, str(exc)[:100])
            return False


async def enrich_tenders(tenders):
    # type: (List[RawTender]) -> int
    """Enrich tenders with missing fields via AI.

    Only processes tenders that are missing organization, price, or deadline.
    Returns count of enriched tenders.
    """
    if not settings.openrouter_api_key:
        return 0

    # Filter: only enrich tenders missing critical fields
    needs_enrichment = [
        t for t in tenders
        if (not t.organization or t.price is None or not t.deadline)
        and t.title  # must have at least a title
        and t.message_type == "tender"  # don't re-enrich customer_requests (already AI-processed)
    ]

    if not needs_enrichment:
        return 0

    # Cap at 200 per cycle to control costs
    if len(needs_enrichment) > 200:
        # Prioritize tenders with the most missing fields
        needs_enrichment.sort(
            key=lambda t: sum([
                not t.organization,
                t.price is None,
                not t.deadline,
            ]),
            reverse=True,
        )
        needs_enrichment = needs_enrichment[:200]

    logger.info("[Enricher] Processing %d tenders with missing fields", len(needs_enrichment))

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    async with httpx.AsyncClient() as client:
        tasks = [_enrich_one(t, client, semaphore) for t in needs_enrichment]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched = sum(1 for r in results if r is True)
    return enriched
