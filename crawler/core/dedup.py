"""Cross-source tender deduplication.

Groups tenders that appear on multiple platforms into clusters.
Uses fuzzy matching: normalized organization + title keywords + deadline proximity.
"""

import logging
import re
import uuid
from typing import Dict, List, Optional, Set, Tuple

from crawler.core.models import RawTender

logger = logging.getLogger(__name__)

# Minimum similarity to consider two tenders as duplicates
_TITLE_OVERLAP_THRESHOLD = 0.5  # 50% of significant words must match


def _normalize_org(org: str) -> str:
    """Normalize organization name for comparison.

    Removes legal forms, quotes, extra spaces.
    'ООО "Рога и Копыта"' -> 'рога копыта'
    """
    if not org:
        return ""
    text = org.lower()
    # Remove legal forms
    for form in (
        "ooo", "ооо", "ао", "чп", "ип", "гуп", "муп",
        "акционерное общество", "общество с ограниченной",
        "государственное унитарное", "частное предприятие",
        '"', "'", "«", "»", "(", ")", ".", ",",
    ):
        text = text.replace(form, "")
    # Collapse whitespace
    return " ".join(text.split())


def _extract_significant_words(title: str) -> Set[str]:
    """Extract significant words from title, ignoring stop words."""
    stop_words = {
        "на", "в", "и", "по", "для", "от", "до", "с", "из", "к",
        "о", "об", "за", "при", "над", "под", "между",
        "the", "of", "for", "and", "to", "in", "a", "an",
        "закупка", "тендер", "лот", "услуги", "товар", "работы",
        "приобретение", "поставка", "оказание", "выполнение",
    }
    words = set(re.findall(r"[а-яёa-z0-9]+", title.lower()))
    return words - stop_words


def _title_similarity(words_a: Set[str], words_b: Set[str]) -> float:
    """Jaccard-like similarity between two word sets."""
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    # Use min denominator (not union) — if small title is subset of large, it's a match
    min_size = min(len(words_a), len(words_b))
    if min_size == 0:
        return 0.0
    return len(intersection) / min_size


def _parse_deadline_rough(deadline: Optional[str]) -> Optional[str]:
    """Extract YYYY-MM-DD from deadline string for proximity check."""
    if not deadline:
        return None
    # Try common formats
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", deadline)
    if m:
        return m.group(0)
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", deadline)
    if m:
        return "%s-%s-%s" % (m.group(3), m.group(2), m.group(1))
    return None


def _deadlines_close(d1: Optional[str], d2: Optional[str]) -> bool:
    """Check if two deadlines are within 3 days of each other."""
    if not d1 or not d2:
        return True  # No deadline = don't use as discriminator
    # Simple string comparison (YYYY-MM-DD format)
    try:
        from datetime import datetime, timedelta
        dt1 = datetime.strptime(d1, "%Y-%m-%d")
        dt2 = datetime.strptime(d2, "%Y-%m-%d")
        return abs((dt1 - dt2).days) <= 3
    except ValueError:
        return True


def find_groups(tenders: List[RawTender]) -> Dict[str, str]:
    """Find groups of duplicate tenders across sources.

    Returns: dict {tender.id: group_uuid}
    Only tenders that have duplicates get a group_id.
    """
    if len(tenders) < 2:
        return {}

    # Pre-compute features
    features = []  # type: List[Tuple[str, str, Set[str], Optional[str]]]
    for t in tenders:
        norm_org = _normalize_org(t.organization)
        title_words = _extract_significant_words(t.title)
        deadline = _parse_deadline_rough(t.deadline)
        features.append((norm_org, t.source, title_words, deadline))

    groups = {}  # type: Dict[str, str]  # tender.id -> group_uuid
    used = set()  # type: Set[int]  # indices already assigned

    for i in range(len(tenders)):
        if i in used:
            continue

        cluster = [i]
        for j in range(i + 1, len(tenders)):
            if j in used:
                continue

            # Different source required (same source = different tenders)
            if features[i][1] == features[j][1]:
                continue

            # Organization must be similar
            org_i, org_j = features[i][0], features[j][0]
            if org_i and org_j:
                # At least one org word must match
                org_words_i = set(org_i.split())
                org_words_j = set(org_j.split())
                if not (org_words_i & org_words_j):
                    continue

            # Title similarity check
            sim = _title_similarity(features[i][2], features[j][2])
            if sim < _TITLE_OVERLAP_THRESHOLD:
                continue

            # Deadline proximity
            if not _deadlines_close(features[i][3], features[j][3]):
                continue

            cluster.append(j)

        # Only create group if multiple tenders matched
        if len(cluster) > 1:
            gid = str(uuid.uuid4())
            for idx in cluster:
                groups[tenders[idx].id] = gid
                used.add(idx)
        else:
            # Mark singleton as used to prevent re-checking in inner loop
            used.add(i)

    if groups:
        n_groups = len(set(groups.values()))
        logger.info(
            "[Dedup] Found %d groups covering %d tenders",
            n_groups, len(groups),
        )

    return groups


def _within_source_key(t: RawTender) -> Tuple[str, str, str]:
    """Generate fingerprint for same-source dedup.

    Cooperation.uz publishes one procurement plan as N rows with unique GUIDs
    but identical (title, organization). Without same-source dedup the alert
    channel gets 64 copies of "Учебники печатные" from one organization.

    Returns ``(source, normalized_org, sorted_significant_words)`` — empty org
    or empty title falls back to original external_id semantics (not deduped).
    """
    org = _normalize_org(t.organization or "")
    words = _extract_significant_words(t.title or "")
    if not org or not words:
        # Fall back to external_id — disables fuzzy dedup for this row
        return (t.source, "", t.id)
    return (t.source, org, " ".join(sorted(words)))


def dedup_within_source(
    tenders: List[RawTender],
    keep_existing_keys: Optional[Set[Tuple[str, str, str]]] = None,
) -> Tuple[List[RawTender], int]:
    """First pass: collapse same-source duplicates by (source, org, title-words).

    Drops a new tender if:
    - Another new tender with the same fingerprint already came first in the batch
    - The fingerprint is in ``keep_existing_keys`` (already alerted recently)

    Returns ``(filtered_list, dropped_count)``.
    """
    if not tenders:
        return [], 0

    seen = set(keep_existing_keys or set())
    out = []
    dropped = 0
    for t in tenders:
        key = _within_source_key(t)
        # Empty org/words → fallback unique key — never collapses
        if key[1] == "" and key[2] == t.id:
            out.append(t)
            continue
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        out.append(t)

    if dropped:
        logger.info(
            "[Dedup] Same-source dedup dropped %d/%d tenders by (source, org, title-words)",
            dropped, len(tenders),
        )
    return out, dropped


def load_recent_alerted_fingerprints(days: int = 7) -> Set[Tuple[str, str, str]]:
    """Load (source, normalized_org, sorted_words) fingerprints of tenders that
    were already sent as alerts in the last ``days`` days.

    Used by the cron-level dedup pass so a position alerted yesterday does not
    re-fire today even if the source publishes it with a fresh GUID.
    Returns an empty set on any Supabase failure (degrades gracefully).
    """
    try:
        from datetime import datetime, timedelta, timezone
        from crawler.config.settings import settings
        from supabase import create_client

        if not settings.supabase_url or not settings.supabase_service_role_key:
            return set()
        client = create_client(settings.supabase_url, settings.supabase_service_role_key)
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        keys: Set[Tuple[str, str, str]] = set()
        page_size = 1000
        offset = 0
        while True:
            page = (
                client.table("tenders")
                .select("source,title,organization,external_id")
                .not_.is_("alert_seq", "null")
                .gte("collected_at", since)
                .range(offset, offset + page_size - 1)
                .execute()
            )
            rows = page.data or []
            for row in rows:
                src = row.get("source") or ""
                title = row.get("title") or ""
                org = row.get("organization") or ""
                org_n = _normalize_org(org)
                words = _extract_significant_words(title)
                if not org_n or not words:
                    continue
                keys.add((src, org_n, " ".join(sorted(words))))
            if len(rows) < page_size:
                break
            offset += page_size
        logger.info("[Dedup] Loaded %d alerted fingerprints from last %dd", len(keys), days)
        return keys
    except Exception as exc:
        logger.warning("[Dedup] Failed to load recent fingerprints: %s", str(exc)[:120])
        return set()


def group_for_alerts(
    new_tenders: List[RawTender],
    all_tenders: List[RawTender],
    recent_alerted_keys: Optional[Set[Tuple[str, str, str]]] = None,
) -> Tuple[List[RawTender], Dict[str, List[str]]]:
    """Deduplicate new tenders for alerting.

    Two passes:
    1. Same-source fuzzy dedup (Cooperation.uz publishes the same procurement
       plan position as N rows with different GUIDs — collapse to one alert).
       Also checks against ``all_tenders`` so a position already alerted
       yesterday doesn't fire again today.
    2. Cross-source clustering (one alert when a tender appears on multiple
       platforms with the same org+title).

    Returns:
        - deduplicated list (one representative per group)
        - dict {representative_id: [list of source names in group]}
    """
    if not new_tenders:
        return [], {}

    # Pass 1: collapse same-source spam against fingerprints of:
    #   (a) tenders that came earlier in this same crawl cycle,
    #   (b) tenders alerted in last 7d (so yesterday's "Календарь" suppresses today's copy).
    new_ids = {t.id for t in new_tenders}
    existing_keys = {
        _within_source_key(t) for t in all_tenders if t.id not in new_ids
    }
    if recent_alerted_keys:
        existing_keys |= recent_alerted_keys
    new_tenders, _dropped = dedup_within_source(new_tenders, existing_keys)
    if not new_tenders:
        return [], {}

    # Pass 2: cross-source clustering (existing behavior)
    groups = find_groups(all_tenders)

    # For new tenders, pick one representative per group
    seen_groups = {}  # type: Dict[str, RawTender]  # group_id -> representative
    group_sources = {}  # type: Dict[str, List[str]]  # repr.id -> [sources]
    result = []  # type: List[RawTender]

    for t in new_tenders:
        gid = groups.get(t.id)
        if gid is None:
            # No group = unique tender
            result.append(t)
            continue

        if gid not in seen_groups:
            # First in group = representative
            seen_groups[gid] = t
            group_sources[t.id] = [t.source]
            result.append(t)
        else:
            # Duplicate — add source to existing representative
            repr_tender = seen_groups[gid]
            if repr_tender.id in group_sources:
                group_sources[repr_tender.id].append(t.source)

    if group_sources:
        logger.info(
            "[Dedup] Alerts deduplicated: %d -> %d (merged %d duplicates)",
            len(new_tenders), len(result),
            len(new_tenders) - len(result),
        )

    return result, group_sources
