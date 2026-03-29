"""Session store for auth tokens — backed by Supabase crawler_settings table.

Tokens are stored as JSON in the `value` column with key `auth_token:{platform}`.
Format: {"token": "...", "expires_at": "ISO8601", "obtained_at": "ISO8601", "source": "manual|auto"}
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class SessionStore:
    """Read/write auth tokens from Supabase crawler_settings."""

    def __init__(self):
        # type: () -> None
        self._client = None

    def _get_client(self):
        """Lazy init Supabase client."""
        if self._client is None:
            from crawler.config.settings import settings
            if not settings.supabase_url or not settings.supabase_service_role_key:
                logger.warning("[SessionStore] No Supabase credentials")
                return None
            from supabase import create_client
            self._client = create_client(
                settings.supabase_url,
                settings.supabase_service_role_key,
            )
        return self._client

    def get_token(self, platform):
        # type: (str) -> Optional[str]
        """Get valid token for platform. Returns None if expired or missing."""
        data = self._read(platform)
        if data is None:
            return None

        # Check expiry
        expires_at = data.get("expires_at")
        if expires_at:
            try:
                exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                if exp < now:
                    logger.warning(
                        "[SessionStore] Token for %s expired at %s",
                        platform, expires_at,
                    )
                    return None
            except (ValueError, TypeError):
                pass

        return data.get("token")

    def set_token(self, platform, token, expires_at=None, source="manual"):
        # type: (str, str, Optional[str], str) -> bool
        """Store token for platform. Returns True on success."""
        client = self._get_client()
        if client is None:
            return False

        key = "auth_token:%s" % platform
        value = json.dumps({
            "token": token,
            "expires_at": expires_at or "",
            "obtained_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
        })

        try:
            # Upsert into crawler_settings
            client.table("crawler_settings").upsert(
                {"key": key, "value": value},
                on_conflict="key",
            ).execute()
            logger.info(
                "[SessionStore] Token saved for %s (expires: %s)",
                platform, expires_at or "unknown",
            )
            return True
        except Exception as exc:
            logger.warning(
                "[SessionStore] Failed to save token for %s: %s",
                platform, str(exc)[:100],
            )
            return False

    def is_expired(self, platform):
        # type: (str) -> bool
        """Check if token is expired or missing."""
        return self.get_token(platform) is None

    def mark_expired(self, platform):
        # type: (str) -> None
        """Mark token as expired by setting expires_at to now."""
        data = self._read(platform)
        if data is None:
            return
        data["expires_at"] = datetime.now(timezone.utc).isoformat()
        client = self._get_client()
        if client:
            key = "auth_token:%s" % platform
            try:
                client.table("crawler_settings").upsert(
                    {"key": key, "value": json.dumps(data)},
                    on_conflict="key",
                ).execute()
            except Exception:
                pass

    def _read(self, platform):
        # type: (str) -> Optional[dict]
        """Read token data from Supabase."""
        client = self._get_client()
        if client is None:
            return None

        key = "auth_token:%s" % platform
        try:
            resp = client.table("crawler_settings").select("value").eq(
                "key", key
            ).execute()
            if resp.data and len(resp.data) > 0:
                return json.loads(resp.data[0]["value"])
        except Exception as exc:
            logger.debug(
                "[SessionStore] Read error for %s: %s",
                platform, str(exc)[:80],
            )
        return None


# Global instance
session_store = SessionStore()
