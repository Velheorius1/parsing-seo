#!/usr/bin/env python3
"""DEPRECATED 2026-04-19 — Mac E-IMZO daemon, replaced by VPS /opt/eimzo/auth.py.

The single source of truth for E-IMZO JWT refresh is now the VPS cron job
``0 */4 * * * /opt/eimzo/run_auth.sh``, which runs E-IMZO Linux under Xvfb
+ fluxbox, auto-types the PIN via xdotool, and writes ``auth_token:ebirja``
to Supabase with ``source=auto-vps-eimzo``. Mac no longer runs anything.

Do NOT start this daemon — it will conflict with the VPS daemon (both write
to the same Supabase row, last writer wins). Kept only for git history and
as a fallback if the VPS auth pipeline ever needs to be reproduced locally.

────────────────────── Original docstring below ──────────────────────

Mac E-IMZO CAPIWS daemon — auto-refreshes JWTs for 4 Uzbek platforms.

Runs persistently on Данияр's Mac. Talks to E-IMZO v6.3.5 via its local
CAPIWS WebSocket (``ws://127.0.0.1:64646/service/cryptapi``), signs auth
challenges using a PFX certificate kept on disk, and writes fresh JWTs to
Supabase ``crawler_settings`` via ``session_store`` — no VPS webhook needed.

PIN flow in v6.3.5:
  The PIN is entered once in the E-IMZO GUI dialog that pops up when
  ``pfx.load_key`` is first called. After that the keyId stays loaded in
  E-IMZO's process memory, and the daemon persists it to ``crawler_settings``.
  On restart the daemon reuses the cached keyId silently; a fresh PIN prompt
  only appears if E-IMZO itself was restarted (app quit / Mac reboot).

Supported platforms (from ``crawler.auth_eimzo.PLATFORMS``):
  - ebirja         : E-Birja (Tashkent Exchange)
  - hayotbirja     : Hayot Birja
  - xt-xarid       : XT-Xarid (Reverse Auctions)
  - xarid-ebirja   : Xarid.E-Birja (Госзакупки buyer-side)

Start:
    bash crawler/scripts/start_eimzo_daemon.sh

Stop:
    Ctrl+C inside tmux, or ``kill -TERM <pid>``

Environment:
  E_IMZO_KEY_TIN            — TIN of the PFX certificate to sign with (required)
  E_IMZO_PLATFORMS          — CSV of platforms; default: all known platforms
  EIMZO_DAEMON_REFRESH_SECONDS — refresh interval; default: 14400 (4h)

Security (see DECISIONS.md RISK-3):
  Never logs resp.json(), resp.text, resp.headers, PKCS7 content, or JWT value.
  The PIN is never seen by the daemon — E-IMZO collects it directly from the
  user via its own GUI.
"""

import argparse
import asyncio
import base64
import fcntl
import json
import logging
import os
import signal
import sys
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# Allow ``python3 crawler/scripts/mac_eimzo_daemon.py`` from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import httpx
import websockets

from crawler.auth.constants import HEARTBEAT_KEY
from crawler.auth.session_store import session_store
from crawler.auth_eimzo import (
    PLATFORMS,
    TokenExtractionError,
    _decode_jwt_header,
    _is_jwt,
)
from crawler.config.settings import settings

logger = logging.getLogger(__name__)

LOCK_PATH = "/tmp/eimzo_daemon.lock"
PIDFILE_PATH = "/tmp/eimzo_daemon.pid"
# E-IMZO v6.3.5 CAPIWS endpoint. The non-TLS port serves ws://; the TLS port (64443) serves
# wss:// but requires Origin=https://<host-in-plist>. We pin http://127.0.0.1 (first entry in
# the whitelist) since the daemon runs on Mac loopback.
CAPIWS_URL = "ws://127.0.0.1:64646/service/cryptapi"
CAPIWS_ORIGIN = "http://127.0.0.1"
# APIKEY hash for domain "127.0.0.1" from ~/Library/Preferences/uz.yt.eimzo.plist
# (signed by E-IMZO vendor, rotates with E-IMZO updates). Must be re-registered each WS session.
CAPIWS_APIKEY_DOMAIN = "127.0.0.1"
CAPIWS_APIKEY_HASH = (
    "A7BCFA5D490B351BE0754130DF03A068F855DB4333D43921125B9CF2670EF6A4"
    "0370C646B90401955E1F7BC9CDBF59CE0B2C5467D820BE189C845D0B79CFC96F"
)
DEFAULT_REFRESH_SECONDS = 14400  # 4 hours
CAPIWS_TIMEOUT = 30  # seconds per WS operation
HTTP_TIMEOUT = 15  # seconds per backend HTTP call
FAIL_ALERT_THRESHOLD = 3  # consecutive failures per platform → TG alert

# Per-platform backend URLs for challenge + login.
# ``None`` entries mean the backend path isn't verified yet; daemon skips those
# platforms at startup with a warning. Fill in after browser DevTools inspection.
PLATFORM_BACKENDS = {
    "ebirja": {
        "challenge": "https://app.ebirja.uz/backend/auth/challenge",
        "login": "https://app.ebirja.uz/backend/auth/login",
    },
    "hayotbirja": {
        "challenge": None,  # TODO: verify with browser DevTools Network tab
        "login": None,
    },
    "xt-xarid": {
        "challenge": None,  # TODO: verify with browser DevTools Network tab
        "login": None,
    },
    "xarid-ebirja": {
        "challenge": None,  # TODO: verify with browser DevTools Network tab
        "login": None,
    },
}


# ── Exceptions ────────────────────────────────────────────────────

class DaemonLockHeldError(Exception):
    """Another daemon instance already holds /tmp/eimzo_daemon.lock."""
    pass


# ── JWT exp claim helper (M6) ─────────────────────────────────────

def _expires_at_from_jwt(token):
    # type: (str) -> Optional[str]
    """Decode JWT payload and return ISO8601 ``exp`` as UTC. None if missing/invalid."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1] + "=="
        raw = base64.urlsafe_b64decode(payload_b64)
        data = json.loads(raw)
        exp = data.get("exp") if isinstance(data, dict) else None
        if not isinstance(exp, (int, float)):
            return None
        return datetime.fromtimestamp(int(exp), tz=timezone.utc).isoformat()
    except Exception:
        return None


# ── Lock + pidfile (RISK-2 mitigation) ────────────────────────────

def acquire_lock():
    # type: () -> int
    """Acquire exclusive flock on LOCK_PATH. Raises DaemonLockHeldError if held."""
    fd = os.open(LOCK_PATH, os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise DaemonLockHeldError(LOCK_PATH)
    os.ftruncate(fd, 0)
    os.write(fd, ("%d\n" % os.getpid()).encode())
    os.fsync(fd)
    try:
        with open(PIDFILE_PATH, "w") as pf:
            pf.write("%d\n" % os.getpid())
    except OSError as exc:
        logger.warning("[EimzoDaemon] Could not write pidfile %s: %s", PIDFILE_PATH, str(exc)[:80])
    return fd


def release_lock(fd):
    # type: (int) -> None
    """Release flock and remove pidfile. Idempotent."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        os.close(fd)
    except Exception:
        pass
    try:
        os.unlink(PIDFILE_PATH)
    except OSError:
        pass


# ── CAPIWS protocol helpers ───────────────────────────────────────

class CapiwsSession(object):
    """Logical CAPIWS session. In E-IMZO v6.3.5 every CAPIWS call is a fresh WS
    connection (the server closes the socket after each response) plus an
    ``apikey`` re-registration using the Origin header. This class hides that
    from callers — they see a ``ws``-like object that can be passed around and
    passed to ``capiws_call``.

    Callers may inspect ``.closed`` (always False until ``close()`` is called).
    ``close()`` is a no-op: no persistent socket is held.
    """

    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True

    async def raw_call(self, payload, timeout):
        # type: (dict, int) -> dict
        """Open a WS, register apikey (scoped by Origin), send payload, close.

        Uses two short-lived connections: one to register the domain/apikey
        pair, one for the actual call. Any earlier registration is invalidated
        by the server as soon as the ws closes, so we re-register each time.
        """
        url = CAPIWS_URL
        # 1. Register apikey so the server accepts our Origin.
        async with websockets.connect(
            url, open_timeout=CAPIWS_TIMEOUT, origin=CAPIWS_ORIGIN
        ) as ws:
            await asyncio.wait_for(
                ws.send(json.dumps({
                    "name": "apikey",
                    "arguments": [CAPIWS_APIKEY_DOMAIN, CAPIWS_APIKEY_HASH],
                })),
                timeout=CAPIWS_TIMEOUT,
            )
            _ = await asyncio.wait_for(ws.recv(), timeout=CAPIWS_TIMEOUT)
        # 2. Actual plugin call — server still trusts this Origin.
        async with websockets.connect(
            url, open_timeout=CAPIWS_TIMEOUT, origin=CAPIWS_ORIGIN
        ) as ws2:
            await asyncio.wait_for(ws2.send(json.dumps(payload)), timeout=timeout)
            raw = await asyncio.wait_for(ws2.recv(), timeout=timeout)
            return json.loads(raw)


async def capiws_call(ws, plugin, name, args, timeout=None):
    # type: (CapiwsSession, str, str, list, Optional[int]) -> dict
    """Send one CAPIWS request and return decoded response dict.

    Never logs ``args`` or full ``data`` — they may contain PIN/PKCS7/signatures.
    Logs only plugin/name and a short error ``reason`` on failure.

    Payload uses the v6.3.5 field name ``arguments`` (legacy E-IMZO used ``args``).
    """
    payload = {"plugin": plugin, "name": name, "arguments": list(args)}
    t = int(timeout) if timeout else CAPIWS_TIMEOUT
    data = await ws.raw_call(payload, timeout=t)
    if not data.get("success", False):
        reason = str(data.get("reason", "unknown"))[:80]
        raise RuntimeError(
            "[EimzoDaemon] CAPIWS %s/%s failed: %s" % (plugin, name, reason)
        )
    return data


# ── Platform refresh ──────────────────────────────────────────────

async def refresh_platform(http, ws, platform_id, signer_id, dry_run=False):
    # type: (httpx.AsyncClient, Any, str, str, bool) -> bool
    """Refresh JWT for one platform. Returns True on success.

    Flow: GET challenge → CAPIWS create_pkcs7 → POST login → validate JWT → save.
    Never logs response bodies, headers, PKCS7, or token value.
    """
    if ws is None:
        raise RuntimeError("[EimzoDaemon] CAPIWS connection unavailable")

    backend = PLATFORM_BACKENDS.get(platform_id) or {}
    if not backend.get("challenge") or not backend.get("login"):
        return False

    platform = PLATFORMS.get(platform_id)
    if platform is None:
        logger.warning("[EimzoDaemon] Unknown platform %s", platform_id)
        return False

    # Step 1: fetch challenge
    try:
        resp = await http.get(backend["challenge"], timeout=HTTP_TIMEOUT)
    except Exception as exc:
        logger.warning(
            "[EimzoDaemon] Challenge request failed for %s: %s",
            platform_id, str(exc)[:80],
        )
        return False
    if resp.status_code != 200:
        logger.warning(
            "[EimzoDaemon] Challenge failed for %s (status=%d)",
            platform_id, resp.status_code,
        )
        return False
    try:
        body = resp.json()
    except ValueError:
        logger.warning("[EimzoDaemon] Challenge body for %s is not JSON", platform_id)
        return False
    challenge = body.get("challenge")
    if not challenge and isinstance(body.get("data"), dict):
        challenge = body["data"].get("challenge")
    if not challenge:
        logger.warning("[EimzoDaemon] No challenge field in response for %s", platform_id)
        return False

    # Step 2: sign via CAPIWS
    if isinstance(challenge, str):
        chal_bytes = challenge.encode("utf-8")
    else:
        chal_bytes = bytes(challenge)
    chal_b64 = base64.b64encode(chal_bytes).decode("ascii")
    try:
        # v6.3.5 create_pkcs7 takes (data_64, keyId, detached). Detached "yes"
        # produces a PKCS7 envelope containing the signature but not the payload —
        # standard for auth-challenge flows.
        sig_resp = await capiws_call(ws, "pkcs7", "create_pkcs7", [chal_b64, signer_id, "yes"])
    except Exception as exc:
        logger.warning(
            "[EimzoDaemon] PKCS7 sign failed for %s: %s",
            platform_id, str(exc)[:80],
        )
        return False
    pkcs7 = sig_resp.get("pkcs7_64")
    if not pkcs7:
        logger.warning("[EimzoDaemon] PKCS7 sign returned empty for %s", platform_id)
        return False
    logger.info(
        "[EimzoDaemon] PKCS7 signed for %s (sig_len=%d)",
        platform_id, len(pkcs7),
    )

    # Step 3: exchange signature for JWT
    try:
        login_resp = await http.post(
            backend["login"],
            json={"pkcs7": pkcs7},
            timeout=HTTP_TIMEOUT,
        )
    except Exception as exc:
        logger.warning(
            "[EimzoDaemon] Login request failed for %s: %s",
            platform_id, str(exc)[:80],
        )
        return False
    if login_resp.status_code != 200:
        logger.warning(
            "[EimzoDaemon] Login failed for %s (status=%d)",
            platform_id, login_resp.status_code,
        )
        return False
    try:
        lb = login_resp.json()
    except ValueError:
        logger.warning("[EimzoDaemon] Login body for %s is not JSON", platform_id)
        return False
    token = lb.get("token") or lb.get("access_token")
    if not token and isinstance(lb.get("data"), dict):
        token = lb["data"].get("token") or lb["data"].get("access_token")

    # Step 4: validate JWT (RISK-5)
    if not token or not _is_jwt(token):
        logger.warning("[EimzoDaemon] Login for %s returned non-JWT token", platform_id)
        raise TokenExtractionError(platform_id)

    header = _decode_jwt_header(token) or {}
    alg = header.get("alg")
    # Prefer JWT's own ``exp`` claim (M6). Fall back to ttl_hours only if absent/invalid.
    expires_at = _expires_at_from_jwt(token) or (
        datetime.now(timezone.utc) + timedelta(hours=int(platform.get("ttl_hours", 5)))
    ).isoformat()
    logger.info(
        "[EimzoDaemon] JWT refreshed for %s (len=%d, alg=%s, exp=%s)",
        platform_id, len(token), alg, expires_at,
    )

    if dry_run:
        logger.info("[EimzoDaemon] --dry-run: skipping set_token for %s", platform_id)
        return True

    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(
        None, session_store.set_token, platform_id, token, expires_at, "mac-daemon",
    )
    return bool(ok)


# ── Alert counters (RISK-5 AC-5.2) ────────────────────────────────

FAIL_COUNTERS = {}  # type: Dict[str, int]


async def check_and_alert_platform(platform_id, succeeded):
    # type: (str, bool) -> None
    """Track consecutive failures. Fire TG alert on the FAIL_ALERT_THRESHOLD'th miss."""
    if succeeded:
        FAIL_COUNTERS[platform_id] = 0
        return
    FAIL_COUNTERS[platform_id] = FAIL_COUNTERS.get(platform_id, 0) + 1
    if FAIL_COUNTERS[platform_id] != FAIL_ALERT_THRESHOLD:
        return
    await _send_tg_alert(
        "[EimzoDaemon] %d consecutive refresh failures for %s — "
        "investigate USB token / E-IMZO / network." % (FAIL_ALERT_THRESHOLD, platform_id)
    )


async def _send_tg_alert(text):
    # type: (str) -> None
    """Post an alert to Telegram via bot token from settings. Silent on config gaps."""
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.warning("[EimzoDaemon] TG alert skipped — bot token/chat id missing")
        return
    bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(bot_url, json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": text,
                "disable_notification": False,
            })
    except Exception as exc:
        logger.warning("[EimzoDaemon] TG alert send failed: %s", str(exc)[:80])


# ── Cycle + heartbeat (RISK-7 mitigation) ─────────────────────────

async def run_cycle(http, ws, signer_id, platforms, dry_run=False):
    # type: (httpx.AsyncClient, Any, str, List[str], bool) -> Dict[str, Any]
    """Run one refresh cycle. Returns stats dict for the heartbeat payload."""
    refreshed = []  # type: List[str]
    failures = []  # type: List[Dict[str, str]]
    for pid in platforms:
        try:
            ok = await refresh_platform(http, ws, pid, signer_id, dry_run=dry_run)
            if ok:
                refreshed.append(pid)
            else:
                failures.append({"platform": pid, "error": "refresh_returned_false"})
        except TokenExtractionError as exc:
            failures.append({"platform": pid, "error": "non_jwt_token:%s" % str(exc)[:40]})
        except Exception as exc:
            failures.append({"platform": pid, "error": str(exc)[:80]})

    if not failures:
        status = "ok"
    elif refreshed:
        status = "partial"
    else:
        status = "fail"
    return {
        "cycle_status": status,
        "platforms_refreshed": refreshed,
        "failures": failures,
    }


async def write_heartbeat(daemon_instance_id, cycle_stats):
    # type: (str, Dict[str, Any]) -> None
    """Persist end-of-cycle heartbeat to crawler_settings.HEARTBEAT_KEY."""
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "cycle_status": cycle_stats["cycle_status"],
        "platforms_refreshed": cycle_stats["platforms_refreshed"],
        "failures": cycle_stats["failures"],
        "daemon_instance_id": daemon_instance_id,
        "pid": os.getpid(),
    }
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, session_store.set_setting, HEARTBEAT_KEY, payload)
    except AttributeError:
        logger.error(
            "[EimzoDaemon] session_store.set_setting() missing — heartbeat not persisted"
        )
    except Exception as exc:
        logger.warning("[EimzoDaemon] Heartbeat write failed: %s", str(exc)[:80])


# ── Main loop ─────────────────────────────────────────────────────

# Supabase settings key: persists the E-IMZO keyId between daemon restarts so
# we don't have to re-prompt the PIN via the GUI unless the kernel forgot it
# (E-IMZO app restart / Mac reboot / unload_key).
EIMZO_KEY_ID_SETTING = "eimzo_loaded_key_id"

# Offset inside a PFX certificate's ``alias`` field where the owner's TIN lives
# in v6.3.5 (example: "...1.2.860.3.16.1.1=303902204,serialnumber=...").
_TIN_OID = "1.2.860.3.16.1.1"


def _tin_from_alias(alias):
    # type: (str) -> Optional[str]
    """Parse the TIN out of a PFX certificate alias (RDN-ish string)."""
    if not alias:
        return None
    # Fields are comma-separated ``key=value`` pairs; TIN OID is unique.
    marker = _TIN_OID + "="
    idx = alias.find(marker)
    if idx < 0:
        return None
    tail = alias[idx + len(marker):]
    end = tail.find(",")
    return (tail[:end] if end >= 0 else tail).strip()


def _save_key_id(key_id):
    # type: (str) -> None
    """Persist keyId so the next daemon start can skip the GUI PIN dialog."""
    try:
        session_store.set_setting(EIMZO_KEY_ID_SETTING, {"key_id": key_id})
    except Exception as exc:
        logger.warning("[EimzoDaemon] Could not persist keyId: %s", str(exc)[:80])


def _load_cached_key_id():
    # type: () -> Optional[str]
    try:
        raw = session_store.get_setting(EIMZO_KEY_ID_SETTING)
    except Exception:
        return None
    if isinstance(raw, dict):
        kid = raw.get("key_id")
        return kid if isinstance(kid, str) and kid else None
    return None


async def _probe_key_id(session, key_id):
    # type: (CapiwsSession, str) -> bool
    """Cheap sanity check that a cached keyId is still loaded & unlocked in E-IMZO.

    Signs a 1-byte payload. Success → key is alive and unlocked (no PIN needed).
    Failure reasons we've observed in v6.3.5:
      - 'Ввод пароля отменен' (-5000) — key slot exists but PIN is locked again,
        which happens after E-IMZO's idle timeout or if the original load_key
        was canceled. Caller should fall through to a fresh load_key flow.
      - 'Key not loaded' / 'Unknown keyId' — E-IMZO/OS restart flushed memory.
    """
    try:
        probe = base64.b64encode(b"\x00").decode("ascii")
        data = await session.raw_call(
            {"plugin": "pkcs7", "name": "create_pkcs7",
             "arguments": [probe, key_id, "yes"]},
            timeout=CAPIWS_TIMEOUT,
        )
        if data.get("success"):
            return True
        logger.info(
            "[EimzoDaemon] Cached keyId probe failed (reason=%s) — will re-load",
            str(data.get("reason", "unknown"))[:80],
        )
        return False
    except Exception as exc:
        logger.info(
            "[EimzoDaemon] Cached keyId probe raised %s — will re-load",
            str(exc)[:80],
        )
        return False


async def _open_capiws_session(tin):
    # type: (str) -> tuple
    """Ensure a PFX key matching ``tin`` is loaded in E-IMZO; return (session, keyId).

    Fast path: if a cached keyId from a previous daemon run still works, reuse it
    (no GUI prompt). Slow path: list_disks → list_certificates → load_key, which
    opens the E-IMZO PIN dialog. The caller (or a human) enters the PIN once,
    after which the key stays loaded in E-IMZO memory.
    """
    session = CapiwsSession()

    cached = _load_cached_key_id()
    if cached and await _probe_key_id(session, cached):
        logger.info("[EimzoDaemon] Reusing cached keyId (TIN=%s)", tin)
        return session, cached

    # Enumerate disks → certificates → match by TIN.
    disks_resp = await capiws_call(session, "pfx", "list_disks", [])
    disks = disks_resp.get("disks") or []
    match = None  # type: Optional[tuple]
    for disk in disks:
        certs_resp = await capiws_call(session, "pfx", "list_certificates", [disk])
        for cert in certs_resp.get("certificates") or []:
            if _tin_from_alias(cert.get("alias", "")) == str(tin):
                match = (
                    cert.get("disk") or disk,
                    cert.get("path", "") or "",
                    cert.get("name", ""),
                    cert.get("alias", ""),
                )
                break
        if match:
            break

    if not match:
        raise RuntimeError(
            "[EimzoDaemon] No PFX certificate with TIN=%s in E-IMZO (checked %d disk(s))"
            % (tin, len(disks))
        )

    disk, path, name, alias = match
    logger.info(
        "[EimzoDaemon] Found certificate (TIN=%s, disk=%s, name=%s) — "
        "PIN prompt will appear in the E-IMZO GUI",
        tin, disk, name,
    )
    # ``load_key`` blocks inside E-IMZO until the user enters the PIN in the
    # GUI (or cancels). We give it a generous timeout so the daemon doesn't
    # drop the request mid-prompt.
    load_resp = await capiws_call(
        session, "pfx", "load_key",
        [disk, path, name, alias],
        timeout=180,
    )
    key_id = load_resp.get("keyId")
    if not key_id:
        raise RuntimeError(
            "[EimzoDaemon] load_key succeeded but returned no keyId: %s"
            % json.dumps(load_resp, ensure_ascii=False)[:200]
        )
    _save_key_id(key_id)
    logger.info("[EimzoDaemon] Key loaded (TIN=%s, keyId=%s…)", tin, key_id[:8])
    return session, key_id


async def main_async(args, stop_event):
    # type: (argparse.Namespace, asyncio.Event) -> int
    daemon_instance_id = str(uuid.uuid4())
    logger.info(
        "[EimzoDaemon] starting (instance_id=%s, pid=%d)",
        daemon_instance_id, os.getpid(),
    )

    tin = os.environ.get("E_IMZO_KEY_TIN")
    if not tin:
        logger.error("[EimzoDaemon] E_IMZO_KEY_TIN env var is required")
        return 2
    # NOTE: E-IMZO v6.3.5 handles the PIN dialog inside its own GUI. We never see
    # or transport the PIN; we just invoke ``pfx.load_key`` and E-IMZO pops a
    # system-level PIN prompt. Once loaded, the keyId survives in E-IMZO's
    # process memory (until the app or Mac restarts), so the daemon can run
    # unattended across many refresh cycles.

    platforms_env = os.environ.get("E_IMZO_PLATFORMS", "")
    platforms = [p.strip() for p in platforms_env.split(",") if p.strip()]
    if not platforms:
        platforms = list(PLATFORMS.keys())
    unknown = [p for p in platforms if p not in PLATFORMS]
    if unknown:
        logger.warning("[EimzoDaemon] Dropping unknown platform ids: %s", ",".join(unknown))
        platforms = [p for p in platforms if p in PLATFORMS]
    if not platforms:
        logger.error("[EimzoDaemon] No valid platforms to refresh")
        return 2

    # Warn once about skipped platforms up-front (instead of every cycle).
    skipped = [
        p for p in platforms
        if not (PLATFORM_BACKENDS.get(p) or {}).get("challenge")
        or not (PLATFORM_BACKENDS.get(p) or {}).get("login")
    ]
    if skipped:
        logger.warning(
            "[EimzoDaemon] Platforms skipped (backend URLs not verified): %s",
            ",".join(skipped),
        )

    refresh_seconds = int(
        os.environ.get("EIMZO_DAEMON_REFRESH_SECONDS", str(DEFAULT_REFRESH_SECONDS))
    )

    # Initial connect — triggers the GUI PIN dialog the first time, silent if
    # a cached keyId is still valid in E-IMZO memory.
    try:
        ws, signer_id = await _open_capiws_session(tin)
    except Exception as exc:
        logger.error("[EimzoDaemon] Startup failed: %s", str(exc)[:160])
        return 3

    last_stats = None  # type: Optional[Dict[str, Any]]
    try:
        async with httpx.AsyncClient() as http:
            while True:
                # C1: ensure a live CAPIWS session each cycle.
                if ws is None or getattr(ws, "closed", False):
                    try:
                        ws, signer_id = await _open_capiws_session(tin)
                    except Exception as exc:
                        logger.warning(
                            "[EimzoDaemon] CAPIWS reconnect failed: %s",
                            str(exc)[:100],
                        )
                        ws = None
                        # fall through — run_cycle will record failures per platform

                stats = await run_cycle(http, ws, signer_id, platforms, dry_run=args.dry_run)
                last_stats = stats
                refreshed_set = set(stats["platforms_refreshed"])
                for pid in platforms:
                    await check_and_alert_platform(pid, pid in refreshed_set)
                await write_heartbeat(daemon_instance_id, stats)
                logger.info(
                    "[EimzoDaemon] cycle %s — refreshed=%s failures=%d",
                    stats["cycle_status"],
                    stats["platforms_refreshed"],
                    len(stats["failures"]),
                )

                # If the cycle exposed CAPIWS problems, drop the ws so the next
                # iteration forces a reconnect.
                if ws is not None and _cycle_saw_capiws_error(stats):
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    ws = None

                if args.once:
                    return 0 if stats["cycle_status"] == "ok" else 1

                # Sleep interruptibly so SIGTERM returns within a few seconds.
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=refresh_seconds)
                except asyncio.TimeoutError:
                    pass
                if stop_event.is_set():
                    break
    finally:
        # C2: write a shutdown heartbeat so healthcheck can distinguish
        # clean shutdown from daemon crash.
        shutdown_stats = {
            "cycle_status": "shutdown",
            "platforms_refreshed": (last_stats or {}).get("platforms_refreshed", []),
            "failures": (last_stats or {}).get("failures", []),
        }
        try:
            await write_heartbeat(daemon_instance_id, shutdown_stats)
        except Exception:
            pass
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
    return 0


def _cycle_saw_capiws_error(stats):
    # type: (Dict[str, Any]) -> bool
    """Inspect failure reasons for strings that look like WS/CAPIWS problems."""
    markers = ("capiws", "websocket", "connectionclosed", "connection closed")
    for f in stats.get("failures", []) or []:
        err = str(f.get("error", "")).lower()
        if any(m in err for m in markers):
            return True
    return False


async def _run_with_signal_handling(args):
    # type: (argparse.Namespace) -> int
    """Wire SIGTERM/SIGINT into an asyncio Event so main_async can shut down cleanly."""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal(signame):  # type: (str) -> None
        logger.info("[EimzoDaemon] Received %s, initiating shutdown", signame)
        stop_event.set()

    for signame in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, signame), _on_signal, signame)
        except (NotImplementedError, RuntimeError):
            # Windows / rare asyncio runtimes — signal handler wiring not supported.
            pass

    return await main_async(args, stop_event)


def main():
    # type: () -> int
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    ap = argparse.ArgumentParser(description="Mac E-IMZO CAPIWS daemon (DEPRECATED)")
    ap.add_argument("--once", action="store_true",
                    help="Do one refresh cycle and exit (for cron fallback)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch+sign+validate JWT but do NOT save to Supabase")
    ap.add_argument("--force-deprecated", action="store_true",
                    help="Acknowledge that this daemon is deprecated and run anyway")
    args = ap.parse_args()

    if not args.force_deprecated:
        sys.stderr.write(
            "Mac E-IMZO daemon is DEPRECATED as of 2026-04-19.\n"
            "Token refresh now runs on VPS via /opt/eimzo/auth.py cron (every 4h).\n"
            "Running this daemon would write a stale source=mac heartbeat and may\n"
            "race with the VPS daemon (last writer wins on Supabase).\n"
            "\n"
            "If you really need to run it (debugging only), pass --force-deprecated.\n"
        )
        return 1

    try:
        lock_fd = acquire_lock()
    except DaemonLockHeldError:
        logger.info("[EimzoDaemon] Another daemon instance holds the lock, exiting.")
        return 0

    try:
        return asyncio.run(_run_with_signal_handling(args)) or 0
    finally:
        release_lock(lock_fd)


if __name__ == "__main__":
    sys.exit(main())
