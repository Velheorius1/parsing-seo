"""Zero-result tracker — task #6 / RISK-1 mitigation.

Alerts the operator when a source silently returns 0 tenders for 3 consecutive
crawl cycles. Recovery message is sent once when data returns.

Key design choices (see `.claude/teams/feature-eimzo-reliability/DECISIONS.md`):

1. ``skipped_no_auth`` does NOT count as a zero. Token is absent, not the API.
2. Newly-observed sources are exempt for the first 3 cycles (grace period).
3. Alert storm prevention: at most one alert per source until it recovers.
4. State lives in ``crawler_settings`` under ``ZERO_RESULT_STATE_KEY``, accessed
   via ``session_store.get_setting / set_setting`` (see
   ``.conventions/anti-patterns/no-direct-supabase.md``).
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from crawler.auth.constants import ZERO_RESULT_STATE_KEY
from crawler.auth.session_store import session_store
from crawler.config.settings import settings

logger = logging.getLogger(__name__)

# ── Tunables ────────────────────────────────────────────────────────────────
GRACE_CYCLES = 3        # new sources exempt for first N cycles
ALERT_THRESHOLD = 3     # consecutive zeros before we alert
STATE_VERSION = 1

# ── Outcome classification (string keys to stay JSON-serializable) ──────────
OK_WITH_DATA = "ok_with_data"
OK_EMPTY = "ok_empty"
SKIPPED_NO_AUTH = "skipped_no_auth"
ERROR = "error"


def classify_outcome(count, skipped_no_auth, error):
    # type: (int, bool, Optional[str]) -> str
    """Translate runner-provided signals to a single outcome string.

    Precedence: skipped_no_auth > error > empty > with_data.
    Rationale: if auth was missing we never reached the API — treat that
    first, regardless of whether count happened to be 0.
    """
    if skipped_no_auth:
        return SKIPPED_NO_AUTH
    if error:
        return ERROR
    if count > 0:
        return OK_WITH_DATA
    return OK_EMPTY


def _now_iso():
    # type: () -> str
    return datetime.now(timezone.utc).isoformat()


def _empty_source_state():
    # type: () -> Dict
    return {
        "cycles_observed": 0,
        "consecutive_zeros": 0,
        "last_outcome": None,
        "last_observed_at": None,
        "alerted": False,
        "alerted_at": None,
    }


def _load_state():
    # type: () -> Dict
    """Load state dict. Returns a valid v1 shell on miss/corrupt."""
    raw = session_store.get_setting(ZERO_RESULT_STATE_KEY)
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION, "sources": {}}
    # Defensive: ensure "sources" is a dict
    if not isinstance(raw.get("sources"), dict):
        raw["sources"] = {}
    return raw


def _save_state(state):
    # type: (Dict) -> bool
    return session_store.set_setting(ZERO_RESULT_STATE_KEY, state)


async def _send_telegram(text):
    # type: (str) -> bool
    """Send a silent TG message to the alert chat. Returns True on success."""
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.info("[ZeroResult] TG creds missing — skipping alert")
        return False
    bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(bot_url, json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": text,
                "disable_notification": True,
                "protect_content": True,
            })
            return 200 <= resp.status_code < 300
    except Exception as exc:
        logger.warning("[ZeroResult] TG send failed: %s", str(exc)[:80])
        return False


def advance_source_state(prior, outcome):
    # type: (Dict, str) -> Dict
    """Pure-function state transition. Returns the NEW source state.

    Does NOT send alerts — caller decides based on the returned diff.
    Split out so unit tests (task #7) can exercise transitions without I/O.
    """
    st = dict(prior) if prior else _empty_source_state()
    st["last_outcome"] = outcome
    st["last_observed_at"] = _now_iso()

    if outcome == SKIPPED_NO_AUTH:
        # Neither counts as zero nor increments cycles_observed — the source
        # had no chance to speak. Missing token is a separate healthcheck signal.
        return st

    # Any non-skipped outcome counts as a cycle observed.
    st["cycles_observed"] = int(st.get("cycles_observed", 0)) + 1

    if outcome == OK_WITH_DATA:
        st["consecutive_zeros"] = 0
        # Recovery is the CALLER's decision based on prior["alerted"].
        # We just clear the flag here so the next zero streak starts fresh.
        st["alerted"] = False
        st["alerted_at"] = None
    else:
        # OK_EMPTY or ERROR → counts as a zero.
        st["consecutive_zeros"] = int(st.get("consecutive_zeros", 0)) + 1

    return st


def should_alert(prior, new_state):
    # type: (Dict, Dict) -> bool
    """Decide whether to emit a fresh alert for this source.

    Rules:
    - Never alert during grace (cycles_observed < GRACE_CYCLES).
    - Alert exactly once per zero streak (prior.alerted == False).
    - Require ALERT_THRESHOLD consecutive zeros.
    """
    if new_state.get("cycles_observed", 0) < GRACE_CYCLES:
        return False
    if prior and prior.get("alerted"):
        return False
    if new_state.get("consecutive_zeros", 0) < ALERT_THRESHOLD:
        return False
    return new_state.get("last_outcome") in (OK_EMPTY, ERROR)


def should_send_recovery(prior, new_state):
    # type: (Dict, Dict) -> bool
    """Recovery fires once when a previously-alerted source returns data."""
    if not prior or not prior.get("alerted"):
        return False
    return new_state.get("last_outcome") == OK_WITH_DATA


async def track_and_alert(outcomes, dry_run=False):
    # type: (Dict[str, Dict], bool) -> int
    """Update persisted state and send alerts/recoveries.

    Args:
        outcomes: per-source signals from runner, e.g.
            {"ebirja-rs": {"count": 0, "skipped_no_auth": False, "error": None}}
        dry_run: if True, state is NOT persisted and no TG messages are sent.

    Returns: number of TG messages successfully sent (alerts + recoveries).
    """
    if not outcomes:
        return 0

    state = _load_state()
    sources_state = state["sources"]  # type: Dict[str, Dict]

    alerts_to_send = []    # type: List[str]
    recoveries_to_send = []  # type: List[str]

    for sid, signals in outcomes.items():
        outcome = classify_outcome(
            int(signals.get("count", 0)),
            bool(signals.get("skipped_no_auth", False)),
            signals.get("error"),
        )
        prior = sources_state.get(sid)
        new_state = advance_source_state(prior, outcome)

        if should_alert(prior, new_state):
            reason = "ошибка: %s" % signals["error"][:100] if outcome == ERROR else "0 тендеров подряд"
            alerts_to_send.append(
                "🔇 Источник %s молчит %d циклов (%s)"
                % (sid, new_state["consecutive_zeros"], reason)
            )
            new_state["alerted"] = True
            new_state["alerted_at"] = _now_iso()
        elif should_send_recovery(prior, new_state):
            recoveries_to_send.append(
                "🔔 Источник %s снова с данными (count=%d)"
                % (sid, int(signals.get("count", 0)))
            )
            # new_state.alerted was already reset to False inside advance_source_state

        sources_state[sid] = new_state

    sent = 0
    if not dry_run:
        for body in alerts_to_send + recoveries_to_send:
            if await _send_telegram(body):
                sent += 1
        _save_state(state)
    else:
        logger.info(
            "[ZeroResult] dry_run — would send %d alerts + %d recoveries",
            len(alerts_to_send), len(recoveries_to_send),
        )

    if alerts_to_send or recoveries_to_send:
        logger.info(
            "[ZeroResult] %d alerts, %d recoveries (%d sent, dry_run=%s)",
            len(alerts_to_send), len(recoveries_to_send), sent, dry_run,
        )
    return sent
