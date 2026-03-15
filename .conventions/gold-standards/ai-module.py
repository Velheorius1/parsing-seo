"""Gold standard: Python AI module pattern (OpenRouter + Telegram report).

Pattern for modules that call an LLM via OpenRouter and send results to Telegram.
Used by: ai_evaluator.py. Follow this for any new AI-powered analysis module.

Key conventions:
- Once-per-day guard via /tmp marker file
- Async httpx for both OpenRouter and Telegram calls
- settings.* for all config (never hardcode keys)
- Escape Markdown in user-facing text
- dry_run support: log instead of sending
- Never raise — log warnings and return gracefully
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional

import httpx

from crawler.config.settings import settings

logger = logging.getLogger(__name__)

# --- Once-per-day guard ---

_MARKER_FILE = "/tmp/last_module_name.txt"


def _already_ran_today():  # type: () -> bool
    try:
        if os.path.exists(_MARKER_FILE):
            with open(_MARKER_FILE, "r") as f:
                return f.read().strip() == datetime.utcnow().strftime("%Y-%m-%d")
    except Exception:
        pass
    return False


def _mark_done():  # type: () -> None
    try:
        with open(_MARKER_FILE, "w") as f:
            f.write(datetime.utcnow().strftime("%Y-%m-%d"))
    except Exception:
        pass


# --- AI call pattern ---

_PROMPT_TEMPLATE = """System prompt here.

Input data:
{data_json}

Instructions for output format.
/no_think"""


async def _call_openrouter(data):  # type: (dict) -> Optional[str]
    """Call OpenRouter (Qwen/any model). Returns answer or None."""
    if not settings.openrouter_api_key:
        logger.debug("[Module] No OpenRouter API key, skipping")
        return None

    prompt = _PROMPT_TEMPLATE.format(
        data_json=json.dumps(data, ensure_ascii=False, indent=2)
    )

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
                logger.warning("[Module] OpenRouter %d: %s", resp.status_code, resp.text[:100])
                return None

            answer = resp.json()["choices"][0]["message"]["content"] or ""
            # Strip Qwen3 thinking tags
            answer = answer.strip()
            if "<think>" in answer:
                import re
                answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()
            return answer if answer else None

    except Exception as exc:
        logger.warning("[Module] OpenRouter error: %s", str(exc)[:80])
        return None


# --- Telegram send pattern ---

def _escape_markdown(text):  # type: (str) -> str
    """Escape Markdown v1 special chars for Telegram."""
    return text.replace("*", "").replace("_", "").replace("`", "")


async def _send_telegram(text, parse_mode="Markdown"):
    # type: (str, str) -> bool
    """Send message to Telegram alert chat. Returns True on success."""
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        return False

    bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(bot_url, json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_notification": True,
            })
            if resp.status_code == 200:
                return True
            logger.warning("[Module] Telegram %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("[Module] Telegram error: %s", str(exc)[:80])
    return False


# --- Main entry point pattern ---

async def run_module(dry_run=False):
    # type: (bool) -> None
    """Main entry: guard -> compute -> AI call -> format -> send.

    Supports dry_run: logs output instead of sending to Telegram.
    """
    if _already_ran_today():
        logger.debug("[Module] Already ran today, skipping")
        return

    # 1. Compute stats / gather data
    data = {}  # type: dict

    # 2. Call AI for analysis
    analysis = await _call_openrouter(data)

    # 3. Format message
    parts = []  # type: List[str]
    parts.append("*Report Title*")
    if analysis:
        parts.append("")
        parts.append(_escape_markdown(analysis))
    message = "\n".join(parts)

    # 4. Send or log
    if dry_run:
        logger.info("[Module] DRY RUN:\n%s", message)
    else:
        await _send_telegram(message)

    _mark_done()
