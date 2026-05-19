"""Structured JSONL logging for AI relevance decisions — model comparison.

Goal: compare AI models (qwen3-30b-a3b vs deepseek-v4-flash etc) on real
production traffic. One JSONL line per OpenRouter call.

Path: /var/log/parsing-seo-ai-decisions.jsonl (override via PARSING_AI_LOG env).

Schema:
{
  "ts": "2026-05-19T14:30:00Z",
  "model": "deepseek/deepseek-v4-flash:free",
  "role": "fast",                      # "fast" | "max"
  "tender_external_id": "abc123",
  "source": "cooperation",
  "title": "Закупка коробок гофрокартонных",
  "organization": "OOO Romashka",
  "is_relevant": true,
  "score": 75,
  "category": "client",
  "reason": "...",
  "latency_ms": 450,
  "http_status": 200,
  "error": null
}

Read with: scripts/compare_ai_models.py --days 7
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_LOG_PATH = os.environ.get(
    "PARSING_AI_LOG",
    "/var/log/parsing-seo-ai-decisions.jsonl",
)


def log_ai_decision(
    model,            # type: str
    role,             # type: str   # "fast" | "max"
    tender_external_id,  # type: Optional[str]
    source,           # type: str
    title,            # type: str
    organization,     # type: str
    is_relevant,      # type: Optional[bool]
    score,            # type: Optional[int]
    category,         # type: Optional[str]
    reason,           # type: Optional[str]
    latency_ms,       # type: int
    http_status=None, # type: Optional[int]
    error=None,       # type: Optional[str]
):
    # type: (...) -> None
    """Append one decision line to JSONL log. Never raises (logging only)."""
    try:
        line = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model": model,
            "role": role,
            "tender_external_id": tender_external_id,
            "source": source or "",
            "title": (title or "")[:200],
            "organization": (organization or "")[:120],
            "is_relevant": is_relevant,
            "score": score,
            "category": category,
            "reason": (reason or "")[:200],
            "latency_ms": latency_ms,
            "http_status": http_status,
            "error": (error or "")[:200] if error else None,
        }
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("[AI Decision Log] write failed: %s", str(exc)[:80])
