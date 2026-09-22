"""Supabase upsert logic for tenders."""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from crawler.config.settings import settings
from crawler.core.models import RawTender

logger = logging.getLogger(__name__)

TABLE = "tenders"
UPSERT_CONFLICT = "external_id,source"
EXISTING_LOOKUP_BATCH_SIZE = 40



_client = None  # type: ignore[assignment]


@dataclass
class UpsertOutcome:
    """Database write result with backwards-compatible two-value unpacking."""

    total_upserted: int
    new_tenders: List[RawTender]
    deferred_count: int = 0
    failed_count: int = 0

    def __iter__(self):  # type: ignore[no-untyped-def]
        yield self.total_upserted
        yield self.new_tenders


def _get_client():  # type: ignore[no-untyped-def]
    """Lazy-init Supabase client singleton (service_role for writes)."""
    global _client
    if _client is None:
        from supabase import create_client
        _client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _client


def query_with_retry(fn, attempts=3, label="query"):
    # type: (callable, int, str) -> object
    """Run a blocking Supabase call, retrying transient failures before giving up.

    Same shape as feedback.get_active_mutes (2026-07-16): PostgREST returns statement
    timeout 57014 under crawl load — a single-shot query then fails the whole caller
    (missed deadline reminders, false healthcheck FAIL). Retry with the same
    0.4*(n+1) backoff; re-raise the last error after `attempts` so each caller picks
    its own fallback (stale cache / WARN). Synchronous like the mute retry — the
    Supabase `.execute()` it wraps already blocks, so a rare retry sleep changes
    nothing about the concurrency model even on the async deadline path.
    """
    import time
    last_err = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last_err = exc
            logger.warning("[DB] %s failed (attempt %d/%d): %s",
                           label, attempt + 1, attempts, str(exc)[:100])
            if attempt < attempts - 1:
                time.sleep(0.4 * (attempt + 1))
    raise last_err


def iter_rows(table, select, filters=None, page_size=1000, order_col="collected_at",
              label="iter_rows", max_pages=200):
    """Range-paginate a table client-side, yielding pages of rows.

    THE way to sweep a big source: ILIKE on unindexed columns (organization)
    times out with 57014 consistently — the honest pattern is narrow eq-filters
    server-side + everything fuzzy in Python over paged rows. This is the shared
    paginator; four scripts already carry private copies (metrics_tracker,
    impact_report, source_scorecard, recall_effect_check) — new code uses this
    one instead of a fifth.

    filters: list of (method, args) applied to the query builder in order,
    e.g. [("eq", ("source", "ETender UZEX")), ("gte", ("collected_at", iso))].
    """
    from crawler.config.settings import settings  # noqa: F401  (client init path)
    client = _get_client()
    offset = 0
    pages = 0
    while pages < max_pages:
        def _q(o=offset):
            q = client.table(table).select(select)
            for method, args in (filters or []):
                q = getattr(q, method)(*args)
            return q.order(order_col, desc=True).range(o, o + page_size - 1).execute()
        resp = query_with_retry(_q, label="%s p%d" % (label, pages))
        rows = resp.data or []
        if not rows:
            return
        yield rows
        if len(rows) < page_size:
            return
        offset += page_size
        pages += 1


def _tender_to_row(t: RawTender) -> dict:
    """Convert RawTender to a dict matching the Supabase tenders table schema."""
    row = {
        "external_id": t.external_id,
        "title": t.title,
        "organization": t.organization,
        "price": t.price,
        "currency": t.currency,
        "deadline": t.deadline,
        "date_start": t.date_start,
        "date_end": t.date_end,
        "region": t.region,
        "categories": t.categories,
        "source": t.source,
        "source_url": t.source_url,
        "status": t.status,
        "search_text": t.search_text,
        "collected_at": t.collected_at.isoformat(),
    }
    row["message_type"] = t.message_type
    # Persist enriched display fields (Район, Адрес, Количество, Цена/ед., и т.д.)
    # Column added in migration 015; harmless if column absent (Supabase ignores unknown keys).
    if t.extra_info:
        row["extra_info"] = t.extra_info
    # Optional result fields — only include if set
    if t.winner:
        row["winner"] = t.winner
    if t.winning_price is not None:
        row["winning_price"] = t.winning_price
    if t.result_date:
        row["result_date"] = t.result_date
    if t.group_id:
        row["group_id"] = t.group_id
    if t.bid_count is not None:
        row["bid_count"] = t.bid_count
    # AI relevance fields (migration 017). Only set if AI scored the tender.
    if t.relevance_score is not None:
        row["relevance_score"] = t.relevance_score
        if t.relevance_category:
            row["relevance_category"] = t.relevance_category
        if t.relevance_reason:
            row["relevance_reason"] = t.relevance_reason
    return row


def update_relevance_fields(
    external_id: str,
    source: str,
    score: int,
    category: str,
    reason: str,
) -> bool:
    """Best-effort UPDATE of AI relevance fields on an existing tenders row.

    Called by notifier after AI scoring (which happens AFTER initial upsert).
    Returns True on success, False on missing creds / migration not applied /
    network error. Never raises — alerting must continue regardless.
    """
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return False
    try:
        client = _get_client()
        payload = {
            "relevance_score": score,
            "relevance_category": category or None,
            "relevance_reason": (reason or "")[:200] or None,
        }
        client.table(TABLE).update(payload).eq("external_id", external_id).eq("source", source).execute()
        return True
    except Exception as exc:
        msg = str(exc)
        # Graceful fallback: migration 017 not yet applied → skip silently.
        if "relevance_score" in msg or "PGRST204" in msg:
            logger.debug("[DB] relevance_score column missing — skipping update")
            return False
        logger.warning("[DB] update_relevance_fields failed for %s/%s: %s", source, external_id, msg[:120])
        return False


def _get_existing_rows(
    client,  # type: ignore[no-untyped-def]
    tenders: List[RawTender],
) -> Tuple[Dict[Tuple[str, str], Dict[str, Any]], Set[Tuple[str, str]]]:
    """Fetch existing keys and opted-in detail metadata for these tenders.

    Ordinary sources retain the old, light `(external_id, source)` query. Only
    sources explicitly marked `detail_persistence` read the fields needed to
    preserve detail state. A failed lookup is returned as ``unknown`` rather
    than silently treated as a missing row: writing such a batch could erase a
    persisted specification and incorrectly resend an old tender as new.
    """
    existing = {}  # type: Dict[Tuple[str, str], Dict[str, Any]]
    unknown = set()  # type: Set[Tuple[str, str]]
    # Group by source to minimize queries
    sources = set(t.source for t in tenders)
    for source in sources:
        source_ids = [t.external_id for t in tenders if t.source == source]
        retain_detail = any(t.detail_persistence for t in tenders if t.source == source)
        # The filter is encoded into the request URL. 500 ordinary identifiers
        # caused HTTP 414, while even 100 UUID-like B2Biz/Cooperation identifiers
        # produced repeatable proxy 502s. Keep a conservative URL budget here;
        # the extra indexed lookups are cheap compared with silently deferring a
        # whole source chunk.
        size = EXISTING_LOOKUP_BATCH_SIZE
        for i in range(0, len(source_ids), size):
            batch_ids = source_ids[i : i + size]
            try:
                def _lookup():  # type: ignore[no-untyped-def]
                    return (
                        client.table(TABLE)
                        .select("external_id,source,extra_info,search_text" if retain_detail else "external_id,source")
                        .eq("source", source)
                        .in_("external_id", batch_ids)
                        .execute()
                    )
                resp = query_with_retry(
                    _lookup, label="existing %s %d-%d" % (
                        source, i, i + len(batch_ids),
                    ),
                )
                for row in (resp.data or []):
                    existing[(row["external_id"], row["source"])] = row
            except Exception as exc:
                logger.warning("[DB] Failed to check existing for %s: %s", source, str(exc))
                unknown.update((external_id, source) for external_id in batch_ids)
    return existing, unknown


def _merge_detail_text(search_text, detail_text):
    # type: (str, Any) -> str
    """Put stored specification back into the searchable text once, capped.

    Detail goes first because the relevance call reads the first 320 characters;
    title is supplied to that call separately, while the hidden specification is
    often the only evidence that a deliberately vague tender is ours.
    """
    detail = " ".join(str(detail_text or "").split())
    current = " ".join(str(search_text or "").split())
    if not detail or detail.lower() in current.lower():
        return current
    return ("%s | %s" % (detail, current)).strip(" |")[:2000]


def _legacy_detail_text(stored_search_text, incoming_search_text):
    # type: (Any, Any) -> str
    """Conservatively recover pre-C2 detail appended to an old list text.

    Before C2, sources could persist ``list text | specification`` without the
    dedicated JSONB field. Only migrate when the current list text is an exact
    normalized prefix; arbitrary historical words may be an older title or
    category and must never be promoted to a specification.
    """
    stored = " ".join(str(stored_search_text or "").split())
    incoming = " ".join(str(incoming_search_text or "").split())
    if not stored or not incoming or not stored.lower().startswith(incoming.lower()):
        return ""
    residual = stored[len(incoming):].strip(" |·—-\t")
    return residual[:2000]


def _restore_persisted_detail(tenders, existing_rows):
    # type: (List[RawTender], Dict[Tuple[str, str], Dict[str, Any]]) -> None
    """Restore only opted-in detail payload before list data overwrites a row."""
    for tender in tenders:
        if not tender.detail_persistence:
            continue
        row = existing_rows.get((tender.external_id, tender.source)) or {}
        stored = row.get("extra_info") or {}
        if not isinstance(stored, dict):
            continue

        current_extra = dict(tender.extra_info or {})
        detail_text = current_extra.get("_detail_text")
        if not detail_text:
            detail_text = stored.get("_detail_text") or _legacy_detail_text(
                row.get("search_text"), tender.search_text
            )
        if detail_text:
            # An incoming detail is authoritative. Persisted data is restored
            # only for a plain list payload that did not fetch a new card.
            tender.search_text = _merge_detail_text(tender.search_text, detail_text)
            current_extra["_detail_text"] = detail_text

        # Prequalification details use the richer lots payload rather than the
        # generic API string. Preserve it alongside live list metadata, then
        # reconstruct its human-readable positions exactly as replay does.
        live_lots = current_extra.get("lots")
        lots = live_lots if isinstance(live_lots, list) and live_lots else stored.get("lots")
        if tender.source == "UZEX Предквалификации" and isinstance(lots, list):
            from crawler.core.prequal_detail import merged_search_text, positions_from_detail
            merged = merged_search_text(
                tender.search_text, positions_from_detail({"details": lots})
            )
            if merged:
                tender.search_text = merged
            current_extra["lots"] = lots

        tender.extra_info = current_extra


async def upsert_tenders(
    tenders: List[RawTender],
    batch_size: Optional[int] = None,
    dry_run: bool = False,
) -> UpsertOutcome:
    """Upsert tenders into Supabase in batches.

    Returns an outcome that still unpacks as ``(total_upserted, new_tenders)``
    and also exposes the number safely deferred after an uncertain lookup.
    """
    if not tenders:
        return UpsertOutcome(0, [])

    # Deduplicate by (external_id, source) — keep last occurrence
    seen = {}
    for t in tenders:
        seen[(t.external_id, t.source)] = t
    tenders = list(seen.values())
    logger.info("[DB] Deduplicated: %d unique tenders", len(tenders))

    if dry_run:
        logger.info("[DB] DRY RUN: would upsert %d tenders", len(tenders))
        return UpsertOutcome(len(tenders), tenders)

    if not settings.supabase_url or not settings.supabase_service_role_key:
        logger.warning("[DB] Supabase credentials not set, skipping upsert")
        return UpsertOutcome(0, [])

    client = _get_client()

    # Find which tenders are NEW (not in DB yet)
    existing_rows, unknown_keys = _get_existing_rows(client, tenders)
    deferred_count = 0
    if unknown_keys:
        deferred = [t for t in tenders if (t.external_id, t.source) in unknown_keys]
        deferred_count = len(deferred)
        logger.warning("[DB] Deferring %d tenders after failed existence lookup", len(deferred))
        tenders = [t for t in tenders if (t.external_id, t.source) not in unknown_keys]
    if not tenders:
        return UpsertOutcome(0, [], deferred_count)
    existing_keys = set(existing_rows.keys())
    _restore_persisted_detail(tenders, existing_rows)
    candidate_new_tenders = [
        t for t in tenders
        if (t.external_id, t.source) not in existing_keys
    ]
    new_keys = {(t.external_id, t.source) for t in candidate_new_tenders}
    persisted_new_tenders = []  # type: List[RawTender]
    logger.info("[DB] New tenders: %d (existing: %d)", len(candidate_new_tenders), len(existing_keys))

    size = batch_size or settings.batch_size
    total = 0

    for i in range(0, len(tenders), size):
        batch = tenders[i : i + size]
        rows = [_tender_to_row(t) for t in batch]
        try:
            client.table(TABLE).upsert(
                rows, on_conflict=UPSERT_CONFLICT
            ).execute()
            total += len(batch)
            persisted_new_tenders.extend(
                t for t in batch if (t.external_id, t.source) in new_keys
            )
            # Detail cache is an outbox: acknowledge only after the row,
            # including extra_info._detail_text, is durably accepted by DB.
            detail_keys = [
                (t.source, t.external_id) for t in batch
                if t.detail_persistence and (t.extra_info or {}).get("_detail_text")
            ]
            if detail_keys:
                from crawler.core.detail_cache import ack_details
                if not ack_details(detail_keys):
                    logger.warning("[DB] Could not acknowledge %d cached details", len(detail_keys))
            logger.info(
                "[DB] Upserted batch %d-%d (%d rows)",
                i,
                i + len(batch),
                len(batch),
            )
        except Exception as exc:
            msg = str(exc)
            msg_lower = msg.lower()
            # Fallback: retry without optional columns that may not be deployed
            # yet (extra_info from 015, relevance_* from 017). Strip keys that
            # the error mentions and retry once.
            optional_keys = ("extra_info", "relevance_score", "relevance_category", "relevance_reason")
            missing_keys = [k for k in optional_keys if k in msg]
            if missing_keys and ("PGRST204" in msg or "column" in msg_lower):
                logger.warning(
                    "[DB] columns missing %s — retrying batch %d without them",
                    missing_keys, i,
                )
                stripped = [{k: v for k, v in r.items() if k not in missing_keys} for r in rows]
                try:
                    client.table(TABLE).upsert(
                        stripped, on_conflict=UPSERT_CONFLICT
                    ).execute()
                    total += len(batch)
                    persisted_new_tenders.extend(
                        t for t in batch if (t.external_id, t.source) in new_keys
                    )
                    logger.info(
                        "[DB] Upserted batch %d-%d without %s (%d rows)",
                        i, i + len(batch), missing_keys, len(batch),
                    )
                    continue
                except Exception as exc2:
                    logger.error("[DB] Fallback upsert batch %d failed: %s", i, str(exc2))
            logger.error("[DB] Upsert batch %d failed: %s", i, msg)

    return UpsertOutcome(
        total,
        persisted_new_tenders,
        deferred_count,
        failed_count=len(tenders) - total,
    )
