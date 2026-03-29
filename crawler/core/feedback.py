"""Feedback learning system — records user corrections and provides few-shot examples."""

import logging
from typing import List, Optional

from crawler.core.db import _get_client

logger = logging.getLogger(__name__)

# Cache few-shot examples for the duration of one crawl run
_few_shot_cache = None  # type: Optional[str]
_few_shot_cache_ts = 0.0  # type: float
_FEW_SHOT_TTL = 7200  # 2 hours (matches cron interval)


def get_next_seq(count=1):
    # type: (int) -> int
    """Atomically reserve `count` sequential alert numbers. Returns the first number."""
    try:
        client = _get_client()
        result = client.rpc("get_next_alert_seq", {"p_count": count}).execute()
        if result.data is not None:
            return int(result.data)
    except Exception as exc:
        logger.warning("[Feedback] Failed to get alert seq: %s", str(exc))
    # Fallback: query max alert_seq from tenders
    try:
        client = _get_client()
        result = (
            client.table("tenders")
            .select("alert_seq")
            .not_.is_("alert_seq", "null")
            .order("alert_seq", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            return int(result.data[0]["alert_seq"]) + 1
    except Exception:
        pass
    return 1


def save_alert_seq(tender_external_id, source, alert_seq, telegram_message_id=None):
    # type: (str, str, int, Optional[int]) -> None
    """Update tenders row with alert_seq and telegram_message_id after sending."""
    try:
        client = _get_client()
        update = {"alert_seq": alert_seq}
        if telegram_message_id is not None:
            update["telegram_message_id"] = telegram_message_id
        client.table("tenders").update(update).eq(
            "external_id", tender_external_id
        ).eq("source", source).execute()
    except Exception as exc:
        logger.warning("[Feedback] Failed to save alert_seq %d: %s", alert_seq, str(exc))


def record_feedback(alert_seq, corrected_label, original_label="demand", message_text=None, source=None):
    # type: (int, str, str, Optional[str], Optional[str]) -> bool
    """Record user feedback (correction) for an alert."""
    try:
        client = _get_client()
        # Get tender info if we have alert_seq
        tender_id = None
        if not message_text:
            try:
                r = client.table("tenders").select(
                    "external_id,source,title,message_type"
                ).eq("alert_seq", alert_seq).limit(1).execute()
                if r.data:
                    tender_id = r.data[0]["external_id"]
                    source = source or r.data[0]["source"]
                    message_text = message_text or r.data[0]["title"]
                    original_label = r.data[0].get("message_type", original_label)
            except Exception:
                pass

        client.table("alert_feedback").insert({
            "alert_seq": alert_seq,
            "tender_id": tender_id,
            "original_label": original_label,
            "corrected_label": corrected_label,
            "message_text": message_text,
            "source": source,
        }).execute()
        logger.info("[Feedback] Recorded: #%d -> %s", alert_seq, corrected_label)
        # Invalidate few-shot cache
        global _few_shot_cache
        _few_shot_cache = None
        return True
    except Exception as exc:
        logger.warning("[Feedback] Failed to record feedback: %s", str(exc))
        return False


def get_few_shot_examples(n=5):
    # type: (int) -> str
    """Get N most recent user corrections formatted as few-shot examples for AI prompt.

    Returns empty string if no corrections available.
    Caches result for _FEW_SHOT_TTL seconds to avoid repeated DB queries.
    """
    import time
    global _few_shot_cache, _few_shot_cache_ts

    now = time.time()
    if _few_shot_cache is not None and (now - _few_shot_cache_ts) < _FEW_SHOT_TTL:
        return _few_shot_cache

    try:
        client = _get_client()
        result = (
            client.table("alert_feedback")
            .select("message_text,original_label,corrected_label")
            .not_.is_("message_text", "null")
            .order("created_at", desc=True)
            .limit(n)
            .execute()
        )
        if not result.data:
            _few_shot_cache = ""
            _few_shot_cache_ts = now
            return ""

        examples = []
        for i, row in enumerate(result.data, 1):
            text = (row.get("message_text") or "")[:200]
            corrected = row.get("corrected_label", "?")
            original = row.get("original_label", "?")
            intent = "demand" if corrected == "client" else "ad"
            note = ""
            if original != corrected:
                note = " (user corrected: system said %s but this is %s)" % (original, corrected)
            examples.append(
                "Example %d:\nText: \"%s\"\nCorrect answer: intent: %s%s" % (i, text, intent, note)
            )

        result_str = "\n\n".join(examples)
        _few_shot_cache = result_str
        _few_shot_cache_ts = now
        return result_str
    except Exception as exc:
        logger.debug("[Feedback] Failed to get few-shot examples: %s", str(exc))
        _few_shot_cache = ""
        _few_shot_cache_ts = now
        return ""


def log_message(source, message_id, text, auto_label):
    # type: (str, int, str, str) -> None
    """Log a message to shadow log (message_log table). Fire-and-forget."""
    try:
        client = _get_client()
        client.table("message_log").upsert(
            {
                "source": source,
                "message_id": message_id,
                "text": text[:2000],  # limit text size
                "auto_label": auto_label,
            },
            on_conflict="source,message_id",
        ).execute()
    except Exception as exc:
        # Shadow log is best-effort, don't crash crawler
        logger.debug("[Feedback] Failed to log message: %s", str(exc))


def get_feedback_stats(days=7):
    # type: (int) -> dict
    """Get feedback statistics for the last N days."""
    from datetime import datetime, timedelta, timezone
    try:
        client = _get_client()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        result = (
            client.table("alert_feedback")
            .select("original_label,corrected_label")
            .gte("created_at", cutoff)
            .execute()
        )
        if not result.data:
            return {"total": 0, "false_positives": 0, "false_negatives": 0}

        total = len(result.data)
        false_pos = sum(
            1 for r in result.data
            if r["original_label"] in ("demand", "customer_request") and r["corrected_label"] == "ad"
        )
        false_neg = sum(
            1 for r in result.data
            if r["original_label"] in ("ad", "skipped") and r["corrected_label"] == "client"
        )
        return {
            "total": total,
            "false_positives": false_pos,
            "false_negatives": false_neg,
            "accuracy": round((total - false_pos - false_neg) / total * 100, 1) if total else 0,
        }
    except Exception:
        return {"total": 0, "false_positives": 0, "false_negatives": 0}
