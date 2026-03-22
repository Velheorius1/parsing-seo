"""AI crawl quality evaluator — daily analysis via Qwen (OpenRouter).

Queries Supabase for daily truth (not per-cycle stats), classifies sources
into 3 buckets (ok/idle/error), sends actionable summary to Telegram once per day.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import httpx

from crawler.config.settings import settings

logger = logging.getLogger(__name__)

_EVAL_MARKER = "/tmp/last_tender_eval.txt"

_EVAL_PROMPT = """Ты — аналитик системы мониторинга тендеров в Узбекистане (полиграфия и упаковка).

Вот ДНЕВНАЯ статистика парсинга (данные из БД, не из одного цикла):

{stats_json}

Проанализируй качество данных и дай РОВНО 3 коротких рекомендации:
1. Что улучшить в парсинге (источники с реальными ошибками, пустые поля)
2. Что улучшить в фильтрации (rejection rate, ложные срабатывания)
3. Что улучшить в покрытии (источники с 0 тендеров за день при нормальном >0)

Учти: некоторые источники (Telegram каналы, международные) публикуют редко — 0 за день это нормально.
Формат: пронумерованный список, каждый пункт — 1-2 предложения.
/no_think"""


def _already_evaluated_today():
    # type: () -> bool
    """Check if evaluation was already sent today."""
    try:
        if os.path.exists(_EVAL_MARKER):
            with open(_EVAL_MARKER, "r") as f:
                last_date = f.read().strip()
            return last_date == datetime.utcnow().strftime("%Y-%m-%d")
    except Exception:
        pass
    return False


def _mark_evaluated():
    # type: () -> None
    """Mark today as evaluated."""
    try:
        with open(_EVAL_MARKER, "w") as f:
            f.write(datetime.utcnow().strftime("%Y-%m-%d"))
    except Exception as exc:
        logger.warning("[AI Eval] Failed to write marker: %s", str(exc)[:80])


def _query_daily_stats():
    # type: () -> Optional[dict]
    """Query Supabase for today's tender stats — ground truth."""
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return None

    try:
        from supabase import create_client
        client = create_client(settings.supabase_url, settings.supabase_service_role_key)

        today = datetime.utcnow().strftime("%Y-%m-%d")

        # Total tenders collected today
        resp = (
            client.table("tenders")
            .select("id", count="exact")
            .gte("collected_at", today)
            .execute()
        )
        total_today = resp.count or 0

        # Per-source counts today
        resp = (
            client.table("tenders")
            .select("source")
            .gte("collected_at", today)
            .execute()
        )
        source_counts = {}  # type: Dict[str, int]
        for row in (resp.data or []):
            src = row.get("source", "unknown")
            source_counts[src] = source_counts.get(src, 0) + 1

        # Field quality on today's tenders
        resp = (
            client.table("tenders")
            .select("price,deadline,organization")
            .gte("collected_at", today)
            .limit(2000)
            .execute()
        )
        rows = resp.data or []
        count = len(rows)
        no_price = sum(1 for r in rows if r.get("price") is None)
        no_deadline = sum(1 for r in rows if not r.get("deadline"))
        no_org = sum(1 for r in rows if not r.get("organization"))

        no_price_pct = round(no_price / count * 100, 1) if count else 0.0
        no_deadline_pct = round(no_deadline / count * 100, 1) if count else 0.0
        no_org_pct = round(no_org / count * 100, 1) if count else 0.0

        return {
            "total_today": total_today,
            "source_counts_today": source_counts,
            "field_quality": {
                "no_price_pct": no_price_pct,
                "no_deadline_pct": no_deadline_pct,
                "no_organization_pct": no_org_pct,
                "sample_size": count,
            },
        }
    except Exception as exc:
        logger.warning("[AI Eval] Supabase query failed: %s", str(exc)[:100])
        return None


def _compute_stats(
    source_stats,   # type: Dict[str, int]
    new_count,      # type: int
    alerts_sent,    # type: int
    all_tenders=None,  # type: Optional[List]
    daily_stats=None,  # type: Optional[dict]
):
    # type: (...) -> dict
    """Compute quality stats — prefer Supabase daily data when available."""

    # 3-bucket source classification from this cycle
    sources_ok = []       # type: List[str]
    sources_idle = []     # type: List[str]
    sources_error = []    # type: List[str]

    # Known low-frequency sources (Telegram channels, international orgs)
    low_freq_prefixes = ("tg-", "undp", "ungm", "giz", "osce", "isdb", "world-bank", "grants")

    for sid, count in source_stats.items():
        if count > 0:
            sources_ok.append(sid)
        elif any(sid.startswith(p) for p in low_freq_prefixes):
            sources_idle.append(sid)
        else:
            sources_error.append(sid)

    # Use daily stats from Supabase if available
    if daily_stats:
        total_today = daily_stats["total_today"]
        fq = daily_stats["field_quality"]
        no_price_pct = fq["no_price_pct"]
        no_deadline_pct = fq["no_deadline_pct"]
        no_org_pct = fq["no_organization_pct"]
    else:
        # Fallback to cycle stats
        total_today = sum(source_stats.values())
        no_price_pct = 0.0
        no_deadline_pct = 0.0
        no_org_pct = 0.0
        if all_tenders and len(all_tenders) > 0:
            count = len(all_tenders)
            no_price_pct = round(sum(1 for t in all_tenders if t.price is None) / count * 100, 1)
            no_deadline_pct = round(sum(1 for t in all_tenders if not t.deadline) / count * 100, 1)
            no_org_pct = round(sum(1 for t in all_tenders if not t.organization) / count * 100, 1)

    return {
        "total_today": total_today,
        "new_this_cycle": new_count,
        "alerts_sent": alerts_sent,
        "sources_ok": len(sources_ok),
        "sources_idle": len(sources_idle),
        "sources_error": len(sources_error),
        "error_sources": sources_error[:10],
        "no_price_pct": no_price_pct,
        "no_deadline_pct": no_deadline_pct,
        "no_organization_pct": no_org_pct,
    }


async def _get_ai_recommendations(stats):
    # type: (dict) -> Optional[str]
    """Send stats to Qwen via OpenRouter and get recommendations."""
    if not settings.openrouter_api_key:
        logger.debug("[AI Eval] No OpenRouter API key, skipping AI analysis")
        return None

    prompt = _EVAL_PROMPT.format(stats_json=json.dumps(stats, ensure_ascii=False, indent=2))

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": "Bearer %s" % settings.openrouter_api_key,
                },
                json={
                    "model": settings.ai_relevance_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.3,
                },
            )
            if resp.status_code != 200:
                logger.warning("[AI Eval] OpenRouter %d: %s", resp.status_code, resp.text[:100])
                return None

            data = resp.json()
            raw_answer = data["choices"][0]["message"]["content"] or ""
            answer = raw_answer.strip()
            if "<think>" in answer:
                import re
                answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()
            return answer if answer else None

    except Exception as exc:
        logger.warning("[AI Eval] Error: %s", str(exc)[:80])
        return None


def _format_eval_message(stats, recommendations):
    # type: (dict, Optional[str]) -> str
    """Format evaluation summary for Telegram."""
    parts = []  # type: List[str]
    parts.append("*Анализ качества парсинга*")
    parts.append("")
    parts.append("В БД сегодня: %d тендеров" % stats["total_today"])
    parts.append("Новых за цикл: %d" % stats["new_this_cycle"])
    parts.append("Алертов: %d" % stats["alerts_sent"])
    parts.append("")
    parts.append("Источники: %d ок / %d idle / %d ошибок" % (
        stats["sources_ok"], stats["sources_idle"], stats["sources_error"],
    ))

    if stats["error_sources"]:
        parts.append("Ошибки: %s" % ", ".join(stats["error_sources"][:5]))

    parts.append("")
    parts.append("Без цены: %.0f%%" % stats["no_price_pct"])
    parts.append("Без дедлайна: %.0f%%" % stats["no_deadline_pct"])
    parts.append("Без заказчика: %.0f%%" % stats["no_organization_pct"])

    if recommendations:
        parts.append("")
        parts.append("*Рекомендации AI:*")
        safe_recs = recommendations.replace("*", "").replace("_", "").replace("`", "")
        parts.append(safe_recs)

    return "\n".join(parts)


async def evaluate_crawl_quality(
    source_stats,   # type: Dict[str, int]
    new_count,      # type: int
    alerts_sent,    # type: int
    all_tenders=None,  # type: Optional[List]
    dry_run=False,  # type: bool
):
    # type: (...) -> None
    """Evaluate crawl quality and send daily summary to Telegram.

    Queries Supabase for daily truth, classifies sources into ok/idle/error.
    Only runs once per day (checks /tmp/last_tender_eval.txt).
    """
    if not settings.ai_eval_enabled:
        return

    if _already_evaluated_today():
        logger.debug("[AI Eval] Already evaluated today, skipping")
        return

    # Query Supabase for ground truth
    daily_stats = _query_daily_stats()

    stats = _compute_stats(
        source_stats, new_count, alerts_sent,
        all_tenders=all_tenders, daily_stats=daily_stats,
    )

    # Get AI recommendations
    recommendations = await _get_ai_recommendations(stats)

    # Format and send to Telegram
    message = _format_eval_message(stats, recommendations)

    if dry_run:
        logger.info("[AI Eval] DRY RUN:\n%s", message)
        _mark_evaluated()
        return

    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.debug("[AI Eval] No Telegram config, skipping send")
        _mark_evaluated()
        return

    bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(bot_url, json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_notification": True,
            })
            if resp.status_code == 200:
                logger.info("[AI Eval] Daily evaluation sent to Telegram")
                _mark_evaluated()
            else:
                logger.warning("[AI Eval] Telegram send failed: %d %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("[AI Eval] Telegram error: %s", str(exc)[:80])
