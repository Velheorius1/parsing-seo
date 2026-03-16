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


def group_for_alerts(
    new_tenders: List[RawTender],
    all_tenders: List[RawTender],
) -> Tuple[List[RawTender], Dict[str, List[str]]]:
    """Deduplicate new tenders for alerting.

    Compares new tenders against ALL tenders (including existing).
    Returns:
        - deduplicated list (one representative per group)
        - dict {representative_id: [list of source names in group]}
    """
    if not new_tenders:
        return [], {}

    # Find groups among new tenders + recent existing
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
