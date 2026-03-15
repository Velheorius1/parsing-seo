"""AI crawl quality evaluator — daily analysis via Qwen (OpenRouter).

After each crawl cycle, computes stats and sends them to Qwen for analysis.
Qwen returns 3 actionable recommendations. Summary sent to Telegram once per day.
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

import httpx

from crawler.config.settings import settings

logger = logging.getLogger(__name__)

_EVAL_MARKER = "/tmp/last_tender_eval.txt"

_EVAL_PROMPT = """Ты — аналитик системы мониторинга тендеров в Узбекистане (полиграфия и упаковка).

Вот статистика последнего цикла парсинга:

{stats_json}

Проанализируй качество данных и дай РОВНО 3 коротких рекомендации:
1. Что улучшить в парсинге (источники с ошибками, пустые поля)
2. Что улучшить в фильтрации (rejection rate, ложные срабатывания)
3. Что улучшить в покрытии (недостающие источники, регионы)

Формат: пронумерованный список, каждый пункт — 1-2 предложения.
/no_think"""


def _already_evaluated_today() -> bool:
    """Check if evaluation was already sent today."""
    try:
        if os.path.exists(_EVAL_MARKER):
            with open(_EVAL_MARKER, "r") as f:
                last_date = f.read().strip()
            return last_date == datetime.utcnow().strftime("%Y-%m-%d")
    except Exception:
        pass
    return False


def _mark_evaluated() -> None:
    """Mark today as evaluated."""
    try:
        with open(_EVAL_MARKER, "w") as f:
            f.write(datetime.utcnow().strftime("%Y-%m-%d"))
    except Exception as exc:
        logger.warning("[AI Eval] Failed to write marker: %s", str(exc)[:80])


def _compute_stats(
    source_stats: Dict[str, int],
    new_count: int,
    alerts_sent: int,
    all_tenders: Optional[List] = None,
) -> dict:
    """Compute quality stats from crawl results."""
    total = sum(source_stats.values())
    sources_ok = sum(1 for v in source_stats.values() if v > 0)
    sources_fail = sum(1 for v in source_stats.values() if v == 0)
    failed_sources = [k for k, v in source_stats.items() if v == 0]

    # Compute field quality from tenders if available
    no_price_pct = 0.0
    no_deadline_pct = 0.0
    no_org_pct = 0.0
    if all_tenders and len(all_tenders) > 0:
        no_price = sum(1 for t in all_tenders if t.price is None)
        no_deadline = sum(1 for t in all_tenders if not t.deadline)
        no_org = sum(1 for t in all_tenders if not t.organization)
        count = len(all_tenders)
        no_price_pct = round(no_price / count * 100, 1)
        no_deadline_pct = round(no_deadline / count * 100, 1)
        no_org_pct = round(no_org / count * 100, 1)

    return {
        "total_tenders": total,
        "new_tenders": new_count,
        "alerts_sent": alerts_sent,
        "sources_ok": sources_ok,
        "sources_failed": sources_fail,
        "failed_sources": failed_sources,
        "no_price_pct": no_price_pct,
        "no_deadline_pct": no_deadline_pct,
        "no_organization_pct": no_org_pct,
        "source_breakdown": source_stats,
    }


async def _get_ai_recommendations(stats: dict) -> Optional[str]:
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
            # Strip Qwen3 thinking tags if present
            answer = raw_answer.strip()
            if "<think>" in answer:
                import re
                answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()
            return answer if answer else None

    except Exception as exc:
        logger.warning("[AI Eval] Error: %s", str(exc)[:80])
        return None


def _format_eval_message(stats: dict, recommendations: Optional[str]) -> str:
    """Format evaluation summary for Telegram."""
    parts = []  # type: List[str]
    parts.append("*Анализ качества парсинга*")
    parts.append("")
    parts.append("Всего: %d тендеров (%d новых)" % (stats["total_tenders"], stats["new_tenders"]))
    parts.append("Алертов: %d" % stats["alerts_sent"])
    parts.append("Источники: %d ок / %d ошибок" % (stats["sources_ok"], stats["sources_failed"]))

    if stats["failed_sources"]:
        parts.append("Сбой: %s" % ", ".join(stats["failed_sources"][:5]))

    parts.append("")
    parts.append("Без цены: %.0f%%" % stats["no_price_pct"])
    parts.append("Без дедлайна: %.0f%%" % stats["no_deadline_pct"])
    parts.append("Без заказчика: %.0f%%" % stats["no_organization_pct"])

    if recommendations:
        parts.append("")
        parts.append("*Рекомендации AI:*")
        # Escape markdown in recommendations
        safe_recs = recommendations.replace("*", "").replace("_", "").replace("`", "")
        parts.append(safe_recs)

    return "\n".join(parts)


async def evaluate_crawl_quality(
    source_stats: Dict[str, int],
    new_count: int,
    alerts_sent: int,
    all_tenders: Optional[List] = None,
    dry_run: bool = False,
) -> None:
    """Evaluate crawl quality and send daily summary to Telegram.

    Only runs once per day (checks /tmp/last_tender_eval.txt).
    """
    if not settings.ai_eval_enabled:
        return

    if _already_evaluated_today():
        logger.debug("[AI Eval] Already evaluated today, skipping")
        return

    stats = _compute_stats(source_stats, new_count, alerts_sent, all_tenders)

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
