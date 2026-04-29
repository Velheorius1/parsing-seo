"""Supabase Storage helper for tender preview screenshots.

Bucket: tender-screenshots (public read, service_role write).
Path schema: <safe_source>/<external_id>.jpg

Used by:
- crawler/adapters/spa.py (per-tender screenshot during parse)
- crawler/scripts/repair_today_alerts.py (one-shot repair of broken alerts)
"""

import logging
import re
import time
from typing import Optional

from crawler.core.db import _get_client

logger = logging.getLogger(__name__)

BUCKET = "tender-screenshots"


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ў": "u", "ғ": "g", "қ": "q", "ҳ": "h", "ё": "yo",
}


def _slug(value: str) -> str:
    """ASCII-only filesystem-safe slug for Supabase Storage paths.

    Supabase rejects non-ASCII characters in object keys with "Invalid key" 400.
    """
    out = []
    for ch in value.lower():
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isascii() and (ch.isalnum() or ch in "-_"):
            out.append(ch)
        else:
            out.append("_")
    s = re.sub(r"_+", "_", "".join(out)).strip("_")
    return s[:80] or "unknown"


def upload_screenshot(image_bytes, source, external_id, mime="image/jpeg"):
    # type: (bytes, str, str, str) -> Optional[str]
    """Upload screenshot bytes to Supabase Storage and return the public URL.

    Returns None on failure (caller should log & continue without screenshot).
    Overwrites existing object with the same path (re-runs replace older snapshots).
    """
    if not image_bytes:
        return None

    ext = "jpg" if mime == "image/jpeg" else mime.split("/")[-1]
    path = "{}/{}.{}".format(_slug(source), _slug(str(external_id)), ext)

    client = _get_client()
    storage = client.storage.from_(BUCKET)

    file_options = {"content-type": mime, "upsert": "true", "cache-control": "3600"}

    t0 = time.monotonic()
    try:
        storage.upload(path=path, file=image_bytes, file_options=file_options)
    except Exception as exc:
        logger.warning("[Storage] upload failed for %s/%s: %s", source, external_id, str(exc)[:200])
        return None

    try:
        url = storage.get_public_url(path)
    except Exception as exc:
        logger.warning("[Storage] public_url failed for %s: %s", path, str(exc)[:200])
        return None

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info("[Storage] uploaded %s (%d KB, %d ms) -> %s", path, len(image_bytes) // 1024, elapsed_ms, url)
    return url
