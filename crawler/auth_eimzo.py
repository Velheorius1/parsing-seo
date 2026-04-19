"""One-time E-IMZO token extraction script.

Run this on Mac when a platform JWT expires:
    python3 crawler/auth_eimzo.py

Supported platforms (4):
  - ebirja         : E-Birja (Tashkent Exchange)
  - hayotbirja     : Hayot Birja
  - xt-xarid       : XT-Xarid (Reverse Auctions)
  - xarid-ebirja   : Xarid.E-Birja (Госзакупки buyer-side)

Two modes:
  1. Manual paste  — copy token from browser DevTools
  2. Auto extract  — opens browser, you login via E-IMZO, script captures token

Extracted tokens are validated as JWT (3 segments + decodable header with alg+typ
and alg != "none") BEFORE being written to Supabase. Non-JWT candidates (UUIDs,
refresh tokens, CSRF strings) are rejected. No token value is ever logged —
only platform, length, exp, alg.
"""

import asyncio
import base64
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List, Optional

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)


class TokenExtractionError(Exception):
    """Raised when no localStorage candidate yields a valid JWT."""
    pass


PLATFORMS = {
    "ebirja": {
        "name": "E-Birja (Tashkent Exchange)",
        "url": "https://app.ebirja.uz",
        "token_key": "exchange-web-token",
        "ttl_hours": 5,
    },
    "hayotbirja": {
        "name": "Hayot Birja",
        "url": "https://hayotbirja.uz",
        "token_keys": ["access_token", "auth_token", "jwt"],
        "ttl_hours": 5,  # TODO(task#3): measure empirically via healthcheck probes
    },
    "xt-xarid": {
        "name": "XT-Xarid (Reverse Auctions)",
        "url": "https://xt-xarid.uz",
        "token_keys": ["access_token", "auth_token", "jwt"],
        "ttl_hours": 5,  # TODO(task#3): measure empirically via healthcheck probes
    },
    "xarid-ebirja": {
        "name": "Xarid.E-Birja (Госзакупки buyer-side)",
        "url": "https://xarid.ebirja.uz",
        "token_keys": ["exchange-web-token", "access_token", "jwt"],
        "ttl_hours": 5,  # TODO(task#3): measure empirically via healthcheck probes
    },
}


def _decode_jwt_header(token):
    # type: (str) -> Optional[dict]
    """Decode JWT header (part[0]) as dict. Returns None on any failure."""
    try:
        if not token or token.count(".") != 2:
            return None
        header_b64 = token.split(".")[0]
        # urlsafe_b64decode requires padding — pad with '==' (safe: ignored if not needed)
        padded = header_b64 + "=="
        raw = base64.urlsafe_b64decode(padded)
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        return None
    except Exception:
        return None


def _is_jwt(token):
    # type: (str) -> bool
    """Return True if token looks like a signed JWT.

    Enforces: 3 dot-separated parts, header decodes to dict, contains both
    ``alg`` and ``typ``, and ``alg`` is not the unsigned placeholder ``none``.
    """
    header = _decode_jwt_header(token)
    if header is None:
        return False
    if "typ" not in header:
        return False
    alg = header.get("alg")
    if not alg or str(alg).strip().lower() == "none":
        return False
    return True


def _candidate_keys(platform):
    # type: (dict) -> List[str]
    """Return the list of localStorage keys to try for a platform.

    Supports both schemas:
      - {"token_key": "..."}           → single-key platform (ebirja)
      - {"token_keys": ["a", "b"]}     → multi-candidate platform
    """
    if "token_keys" in platform:
        keys = platform["token_keys"]
        if isinstance(keys, list) and keys:
            return list(keys)
    single = platform.get("token_key")
    if single:
        return [single]
    return []


def _save_validated_token(platform_id, platform, token, key, alg, source):
    # type: (str, dict, str, str, Optional[str], str) -> None
    """Persist an already-JWT-validated token via session_store, log metadata only.

    Callers MUST have already passed ``token`` through ``_is_jwt``. This helper
    never re-validates, never logs the token value — only platform, key name,
    alg, length, and expiry.
    """
    from crawler.auth.session_store import session_store

    ttl = int(platform.get("ttl_hours", 5))
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=ttl)).isoformat()

    logger.info(
        "[EimzoAuth] Using localStorage key %r for %s (JWT validated, alg=%s)",
        key, platform_id, alg,
    )
    logger.info(
        "[EimzoAuth] Token for %s (len=%d, exp=%s)",
        platform_id, len(token), expires_at,
    )

    ok = session_store.set_token(platform_id, token, expires_at, source=source)
    if ok:
        print("\nToken saved for %s" % platform["name"])
        print("Expires: %s (~%dh)" % (expires_at, ttl))
    else:
        print("\nERROR: Failed to save token")


def manual_paste(platform_id):
    # type: (str) -> None
    """Paste token from browser DevTools. Iterates candidate keys until a JWT-valid token is given."""
    platform = PLATFORMS[platform_id]
    keys = _candidate_keys(platform)
    if not keys:
        raise TokenExtractionError(platform_id)

    print("\n--- Manual Token Paste ---")
    print("1. Open %s in browser" % platform["url"])
    print("2. Login via E-IMZO if needed")
    print("3. Open DevTools (F12) > Console")
    print("4. For each key below, run: localStorage.getItem('<key>')")
    print("5. Paste the token (without quotes) when prompted")
    print()

    accepted_token = None
    accepted_key = None
    accepted_alg = None

    for key in keys:
        print("Try localStorage key: %s" % key)
        raw = input("  Paste token for '%s' (blank to skip): " % key).strip().strip('"').strip("'")
        if not raw:
            logger.info(
                "[EimzoAuth] Candidate skipped for %s (key=%s, no input)",
                platform_id, key,
            )
            continue

        if not _is_jwt(raw):
            logger.info(
                "[EimzoAuth] Candidate rejected for %s (key=%s, not a JWT)",
                platform_id, key,
            )
            print("  Rejected: not a JWT. Trying next candidate...")
            continue

        header = _decode_jwt_header(raw) or {}
        accepted_alg = header.get("alg")
        accepted_token = raw
        accepted_key = key
        break

    if accepted_token is None:
        logger.warning(
            "[EimzoAuth] No valid JWT extracted for %s across %d candidate key(s)",
            platform_id, len(keys),
        )
        raise TokenExtractionError(platform_id)

    _save_validated_token(
        platform_id, platform, accepted_token, accepted_key, accepted_alg, "manual",
    )


async def auto_extract(platform_id):
    # type: (str) -> None
    """Open browser, wait for E-IMZO login, capture and validate token."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ERROR: Install playwright first: pip install playwright && playwright install chromium")
        return

    platform = PLATFORMS[platform_id]
    keys = _candidate_keys(platform)
    if not keys:
        raise TokenExtractionError(platform_id)

    print("\nOpening %s..." % platform["url"])
    print("Login via E-IMZO in the browser window.")
    print("The script will capture the token automatically.\n")

    accepted_token = None
    accepted_key = None
    accepted_alg = None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(platform["url"])

        # Poll every 2s, max 5min. Try every candidate key on each poll.
        for _ in range(150):
            await asyncio.sleep(2)
            for key in keys:
                try:
                    candidate = await page.evaluate(
                        "localStorage.getItem('%s')" % key
                    )
                except Exception:
                    candidate = None

                if not candidate:
                    continue

                if not _is_jwt(candidate):
                    logger.info(
                        "[EimzoAuth] Candidate rejected for %s (key=%s, not a JWT)",
                        platform_id, key,
                    )
                    continue

                header = _decode_jwt_header(candidate) or {}
                accepted_alg = header.get("alg")
                accepted_token = candidate
                accepted_key = key
                break

            if accepted_token is not None:
                break

        await browser.close()

    if accepted_token is None:
        logger.warning(
            "[EimzoAuth] No valid JWT extracted for %s across %d candidate key(s) after polling",
            platform_id, len(keys),
        )
        raise TokenExtractionError(platform_id)

    _save_validated_token(
        platform_id, platform, accepted_token, accepted_key, accepted_alg, "auto-playwright",
    )


def main():
    # type: () -> None
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    print("=== E-IMZO Token Extractor ===\n")

    # Platform selection
    print("Platforms:")
    ids = list(PLATFORMS.keys())
    for i, pid in enumerate(ids):
        print("  %d. %s" % (i + 1, PLATFORMS[pid]["name"]))
    choice = input("\nChoose platform [1]: ").strip() or "1"
    try:
        platform_id = ids[int(choice) - 1]
    except (IndexError, ValueError):
        print("Invalid choice")
        return

    # Mode selection
    print("\nMode:")
    print("  1. Paste token from DevTools (simple)")
    print("  2. Open browser, auto-capture (needs Playwright)")
    mode = input("\nChoose mode [1]: ").strip() or "1"

    try:
        if mode == "1":
            manual_paste(platform_id)
        elif mode == "2":
            asyncio.run(auto_extract(platform_id))
        else:
            print("Invalid mode")
    except TokenExtractionError as exc:
        print("\nERROR: Could not extract a valid JWT for %s." % str(exc))
        print("All candidate localStorage keys were tried but none yielded a JWT.")
        print("Verify you are logged in via E-IMZO and retry.")
        sys.exit(1)


if __name__ == "__main__":
    main()
