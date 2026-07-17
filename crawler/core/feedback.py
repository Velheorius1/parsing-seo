"""Feedback learning system — records user corrections and provides few-shot examples."""

import json
import logging
import os
import time
from typing import List, Optional

from crawler.core.db import _get_client

logger = logging.getLogger(__name__)


def _system_verdict(relevance_category, relevance_score):
    # type: (Optional[str], Optional[int]) -> str
    """Compress the system's stored relevance decision into ONE token so a later
    feedback click can be classed honestly as agreement / false-positive / recall-guard.

    Everything in alert_feedback was ALERTED (system deemed it worth showing), so a
    missing verdict defaults to 'alerted' (relevant), never 'unknown'. Score band 70
    ≈ midpoint between the observed avg for category='client' (90) and 'irrelevant' (45).
    """
    cat = (relevance_category or "").strip().lower()
    if cat in ("client", "ad", "irrelevant"):
        return cat
    if relevance_score is not None:
        try:
            return "client" if int(relevance_score) >= 70 else "weak"
        except (TypeError, ValueError):
            pass
    return "alerted"

# Cache few-shot examples for the duration of one crawl run
_few_shot_cache = None  # type: Optional[str]
_few_shot_cache_ts = 0.0  # type: float
_FEW_SHOT_TTL = 7200  # 2 hours (matches cron interval)

# ── Auto-mute learning (deep-think 2026-07-01) ───────────────────────────────
# Feedback-driven noise suppression: when a SOURCE accumulates ❌ with no ✅, its
# items route to the DIGEST instead of push (Tier-1, reversible). Counters live in
# crawler_settings. A single ✅ vetoes the mute (recall guard). Delivery-only —
# muting never stops ingestion, so it is fully reversible.
_MUTE_STATE_KEY = "mute_patterns_v1"
_MUTE_NEG_THRESHOLD = 3  # ❌ needed to auto-mute a source
# Last-known-good mute set, persisted so a transient DB read failure at routing time
# doesn't collapse the mute set to empty (which pushes every muted source). Survives
# across cron crawl processes. See get_active_mutes.
_MUTE_CACHE_FILE = "/opt/parsing-seo/logs/active_mutes_cache.json"


def _bump_mute_pattern(source, corrected_label):
    # type: (Optional[str], str) -> Optional[dict]
    """Increment source-level mute counters from one feedback click.
    Returns {muted, neg, pos, threshold} so the caller can show an immediate effect,
    or None on no-op / failure."""
    if not source:
        return None
    is_neg = corrected_label in ("ad", "irrelevant")
    is_pos = corrected_label == "client"
    if not (is_neg or is_pos):
        return None
    try:
        from crawler.auth.session_store import session_store
        state = session_store.get_setting(_MUTE_STATE_KEY)
        if not isinstance(state, dict):
            state = {}
        srcs = state.setdefault("sources", {})
        c = srcs.setdefault(source, {"neg": 0, "pos": 0})
        c["neg"] = int(c.get("neg", 0)) + (1 if is_neg else 0)
        c["pos"] = int(c.get("pos", 0)) + (1 if is_pos else 0)
        session_store.set_setting(_MUTE_STATE_KEY, state)
        muted = c["neg"] >= _MUTE_NEG_THRESHOLD and c["pos"] == 0
        logger.info("[Mute] %s: neg=%d pos=%d%s", source, c["neg"], c["pos"],
                    " → MUTED (→digest)" if muted else "")
        return {"muted": muted, "neg": c["neg"], "pos": c["pos"], "threshold": _MUTE_NEG_THRESHOLD}
    except Exception as exc:
        logger.warning("[Mute] bump failed: %s", str(exc)[:80])
        return None


def _save_mute_cache(muted):
    # type: (set) -> None
    """Persist the last-known-good mute set to disk (best-effort)."""
    try:
        os.makedirs(os.path.dirname(_MUTE_CACHE_FILE), exist_ok=True)
        with open(_MUTE_CACHE_FILE, "w") as f:
            json.dump(sorted(muted), f, ensure_ascii=False)
    except Exception as exc:
        logger.debug("[Mute] cache save failed: %s", str(exc)[:80])


def _load_mute_cache():
    # type: () -> set
    try:
        with open(_MUTE_CACHE_FILE) as f:
            data = json.load(f)
        return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()


def get_active_mutes():
    # type: () -> set
    """Sources auto-muted by feedback (>=N ❌, 0 ✅). A single ✅ vetoes the mute.

    RESILIENT (2026-07-16 fix). The old body did `except Exception: return set()` — a
    SILENT fail-open. On any transient Supabase read error (statement timeout 57014, seen
    in prod under crawl load) it returned {}, so that crawl muted NOTHING and every muted
    source got pushed individually. The failure logged nothing and [Route] only logged when
    a digest existed, so it was invisible: weeks-old mutes (Мин сельхоз 25❌/0✅ since 06-25)
    still pushed ~100% of the time. Now: retry the read, and on total failure fall back to
    the disk-persisted last-known-good set — NEVER empty. Serving a minutes-stale mute set
    is safe (un-mute happens only via a rare ✅ click) and far better than muting nothing.
    """
    from crawler.auth.session_store import session_store
    last_err = None
    for attempt in range(3):
        try:
            state = session_store.get_setting(_MUTE_STATE_KEY)
            if isinstance(state, dict):
                srcs = state.get("sources", {}) or {}
                muted = {s for s, c in srcs.items()
                         if int(c.get("neg", 0)) >= _MUTE_NEG_THRESHOLD and int(c.get("pos", 0)) == 0}
                _save_mute_cache(muted)
                return muted
            last_err = "get_setting returned non-dict (None/parse-fail/unreachable)"
        except Exception as exc:
            last_err = str(exc)[:100]
        time.sleep(0.4 * (attempt + 1))
    cached = _load_mute_cache()
    logger.warning("[Mute] read FAILED after retries (%s) — using %d cached muted sources (NOT empty)",
                   last_err, len(cached))
    return cached


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
                    "external_id,source,title,relevance_category,relevance_score"
                ).eq("alert_seq", alert_seq).limit(1).execute()
                if r.data:
                    row = r.data[0]
                    tender_id = row["external_id"]
                    source = source or row["source"]
                    message_text = message_text or row["title"]
                    # Store the real system VERDICT (not message_type) so the playbook
                    # can tell an agreement from a correction. Hole A fix (2026-07-16).
                    original_label = _system_verdict(
                        row.get("relevance_category"), row.get("relevance_score"))
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
        mute = _bump_mute_pattern(source, corrected_label)
        # Invalidate few-shot cache
        global _few_shot_cache
        _few_shot_cache = None
        # Dict (truthy) so callers can show the immediate mute effect; still truthy like the old bool.
        return {"ok": True, "source": source, "mute": mute}
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


_playbook_cache = None
_playbook_cache_ts = 0.0


def get_relevance_playbook(limit=20):
    # type: (int) -> str
    """Active classifier_playbook principles formatted for the relevance prompt.
    Empty string when none active (dormant — prompt stays at baseline). Cached 2h."""
    global _playbook_cache, _playbook_cache_ts
    import time
    now = time.time()
    if _playbook_cache is not None and (now - _playbook_cache_ts) < _FEW_SHOT_TTL:
        return _playbook_cache
    out = ""
    try:
        client = _get_client()
        r = (client.table("classifier_playbook")
             .select("taxonomy,principle,example")
             .eq("status", "active")
             .order("support_count", desc=True)
             .limit(limit).execute())
        rows = r.data or []
        if rows:
            lines = []
            for row in rows:
                tx = row.get("taxonomy") or ""
                pr = row.get("principle") or ""
                ex = row.get("example") or ""
                lines.append("- [%s] %s%s" % (tx, pr, (" " + ex) if ex else ""))
            out = "\n".join(lines)
    except Exception as exc:
        logger.warning("[Playbook] load failed: %s", str(exc)[:120])
        out = ""
    _playbook_cache = out
    _playbook_cache_ts = now
    return out


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
        # alert_feedback holds only ALERTED (shown) items, so the two error kinds are:
        #   human=ad/irrelevant                        → false positive (shown, shouldn't have been)
        #   human=client + weak/ad/irrelevant verdict  → false negative (system underrated it)
        # (The old check keyed on original_label='ad'/'skipped', which message_type never
        #  produced → false_negatives were structurally always 0. Hole A fix 2026-07-16.)
        false_pos = sum(
            1 for r in result.data if r["corrected_label"] in ("ad", "irrelevant")
        )
        false_neg = sum(
            1 for r in result.data
            if r["corrected_label"] == "client"
            and (r.get("original_label") or "") in ("ad", "irrelevant", "weak")
        )
        return {
            "total": total,
            "false_positives": false_pos,
            "false_negatives": false_neg,
            "accuracy": round((total - false_pos - false_neg) / total * 100, 1) if total else 0,
        }
    except Exception:
        return {"total": 0, "false_positives": 0, "false_negatives": 0}
