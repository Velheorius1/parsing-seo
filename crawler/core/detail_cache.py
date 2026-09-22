"""Durable handoff for fetched specification text.

Detail endpoints are consumed before the combined database upsert.  The source
high-water mark may therefore advance before a later database failure.  This
small file-backed outbox keeps fetched text until the corresponding tender row
has been written successfully.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import fcntl


def _path(path=None):
    # type: (Optional[Path]) -> Path
    if path is not None:
        return Path(path)
    configured = os.environ.get("PARSING_DETAIL_CACHE_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "data" / "detail-persistence-cache.json"


def _key(source, external_id):
    # type: (str, Any) -> str
    return "%s\x1f%s" % (source, str(external_id))


def _read_unlocked(path):
    # type: (Path) -> Dict[str, Dict[str, Any]]
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): dict(row) for key, row in value.items() if isinstance(row, dict)}


def _write_unlocked(path, value):
    # type: (Path, Dict[str, Dict[str, Any]]) -> None
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _locked(path, mutate=None):
    # type: (Path, Any) -> Dict[str, Dict[str, Any]]
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX if mutate else fcntl.LOCK_SH)
        value = _read_unlocked(path)
        if mutate is not None:
            mutate(value)
            _write_unlocked(path, value)
        return value


def read_details(path=None):
    # type: (Optional[Path]) -> Dict[str, Dict[str, Any]]
    return _locked(_path(path))


def store_detail(source, external_id, detail_text, path=None):
    # type: (str, Any, Any, Optional[Path]) -> bool
    text = " ".join(str(detail_text or "").split())[:2000]
    if not source or not text:
        return False

    def _store(value):
        value[_key(source, external_id)] = {
            "source": source,
            "external_id": str(external_id),
            "detail_text": text,
            "stored_at": datetime.now(timezone.utc).isoformat(),
        }

    try:
        _locked(_path(path), _store)
        return True
    except OSError:
        return False


def restore_details(items, source, id_field="id", path=None):
    # type: (Iterable[Dict[str, Any]], str, str, Optional[Path]) -> int
    cached = read_details(path)
    restored = 0
    for item in items:
        if item.get("_detail_text"):
            continue
        row = cached.get(_key(source, item.get(id_field)))
        if row and row.get("detail_text"):
            item["_detail_text"] = row["detail_text"]
            restored += 1
    return restored


def ack_details(keys, path=None):
    # type: (Iterable[Tuple[str, Any]], Optional[Path]) -> bool
    remove = {_key(source, external_id) for source, external_id in keys}
    if not remove:
        return True

    def _ack(value):
        for key in remove:
            value.pop(key, None)

    try:
        _locked(_path(path), _ack)
        return True
    except OSError:
        return False
