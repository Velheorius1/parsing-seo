"""replay — run tenders through the CURRENT filter stack without sending anything.

The measurement half of the version benchmark (Daniyar 27.07: «в погоне за
улучшениями не сделать краулер тупее»). A historical tender goes through the
exact production stages — prefilter() (the same function send_alerts calls),
then optionally the AI gate and the push/digest routing — and comes back as a
structured verdict: passed or died, at which stage, with what score, to which
tier. customer_audit uses it to answer "would TODAY's crawler catch this bank
tender", version_scorecard uses it to score every crawler version on the frozen
corpus.

Writes NOTHING: no alert_seq (never imported), no relevance persist, no
Telegram, no verifier calls (a historical lot is always "closed" on the
platform — verifying it would fake a recall failure). The AI decision JSONL is
redirected away from the prod comparison log via PARSING_AI_LOG below.

Usage (debug CLI):
  python3 -m crawler.scripts.replay --external-id X --source "ETender UZEX" [--ai]
  python3 -m crawler.scripts.replay --from-json rows.json [--ai] [--as-of 2026-06-01]
"""
import os

# MUST precede any crawler import: ai_decision_log resolves its path at module
# import time, and replay traffic must not pollute the prod model-comparison log.
os.environ.setdefault("PARSING_AI_LOG", "/tmp/replay-ai-decisions.jsonl")

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Set

sys.path.insert(0, "/opt/parsing-seo")

from crawler.core.models import RawTender  # noqa: E402


@dataclass
class ReplayVerdict:
    external_id: str
    source: str
    title: str
    passed_prefilter: bool
    dropped_at_stage: Optional[str]      # DropStage.* or None
    matched_kw: Optional[str]
    uzex_bypass: bool
    is_lead: bool
    ai_score: Optional[int] = None
    ai_category: Optional[str] = None
    ai_error: bool = False               # transport failure → prod would fail-open
    ai_skipped: bool = True              # use_ai=False, bypass, or died earlier
    delivered: Optional[bool] = None     # True/False; None = prefilter-only mode
    route: Optional[str] = None          # "push" | "digest" | None


def row_to_raw_tender(row):
    # type: (dict) -> RawTender
    """DB/platform row -> RawTender, tolerant of the shapes we actually store.

    extra_info arrives as jsonb with int/bool values — RawTender wants
    Dict[str, str] (same trap investigator hit, af1c155): str-coerce.
    """
    extra = {}
    for k, v in (row.get("extra_info") or {}).items():
        if v is None:
            continue
        extra[str(k)] = v if isinstance(v, str) else str(v)
    ext_id = str(row.get("external_id") or row.get("id") or "replay")

    # Паритет с продом для предквалификаций (22.08). Прод дотягивает предмет
    # лота в search_text ДО гейтов (core/prequal_detail), но upsert при каждом
    # краyле перезаписывает search_text списочным значением (одна категория),
    # а extra_info.lots при этом переживает — upsert не пишет пустой extra_info.
    # Без этой склейки replay видел бы «Услуги издательские <заказчик>» там, где
    # прод видел «… | Услуга публикации статьи», и бенчмарк мерил бы не тот
    # конвейер. Берём lots из СЫРОГО extra_info: выше он str-коэрсится, и список
    # превратился бы в строку.
    search_text = row.get("search_text") or ""
    raw_extra = row.get("extra_info") or {}
    if row.get("source") == "UZEX Предквалификации" and isinstance(raw_extra.get("lots"), list):
        from crawler.core.prequal_detail import merged_search_text, positions_from_detail
        merged = merged_search_text(search_text, positions_from_detail({"details": raw_extra["lots"]}))
        if merged:
            search_text = merged

    return RawTender(
        id=ext_id,
        external_id=ext_id,
        title=row.get("title") or "",
        organization=row.get("organization") or "",
        price=row.get("price"),
        currency=row.get("currency") or "UZS",
        deadline=row.get("deadline"),
        source=row.get("source") or "",
        source_url=row.get("source_url") or "",
        status=row.get("status") or "active",
        search_text=search_text,
        message_type=row.get("message_type") or "tender",
        bid_count=row.get("bid_count"),
        extra_info=extra,
    )


def _as_of_value(spec, row_ts):
    # type: (str, Optional[str]) -> Optional[datetime]
    """Resolve the --as-of spec to a datetime for prefilter(now=...).

    "collected_at" → the tender's own collection moment (judge it as of its
    day); "now" → wall clock (judge it as of today); ISO date → that date.
    """
    if spec == "now":
        return None
    if spec == "collected_at":
        if not row_ts:
            return None
        try:
            return datetime.fromisoformat(str(row_ts).replace("Z", "+00:00"))
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(spec)
    except ValueError:
        return None


async def replay_tenders(
    tenders,                 # type: List[RawTender]
    use_ai=False,            # type: bool
    as_of="collected_at",    # type: str
    mutes=None,              # type: Optional[Set[str]]
    keywords=None,           # type: Optional[List[str]]
    tnved_scope=None,        # type: Optional[List[str]]
    collected_at=None,       # type: Optional[dict]
):
    # type: (...) -> List[ReplayVerdict]
    """Mirror of the prod decision path, minus every side effect.

    mutes defaults to an EMPTY set — deliberately not get_active_mutes(), which
    writes a cache file; the caller injects live mutes if the question is
    "would it push through today's mutes" rather than "is the tender catchable".
    collected_at: optional {external_id: iso_ts} for as_of="collected_at".
    """
    from crawler.core.notifier import (
        prefilter, _get_keywords, _load_tnved_scope, _route_to_push,
        _ai_check_relevance, _ai_lead_is_spam,
    )

    if keywords is None:
        keywords = _get_keywords()
    if tnved_scope is None:
        tnved_scope = _load_tnved_scope()
    mutes = set() if mutes is None else mutes
    collected_at = collected_at or {}

    verdicts = []
    client = None
    try:
        if use_ai:
            import httpx
            client = httpx.AsyncClient(timeout=25)

        for t in tenders:
            now = _as_of_value(as_of, collected_at.get(t.external_id))
            pf = prefilter([t], keywords, tnved_scope=tnved_scope, now=now)
            v = pf.verdicts[0]
            rv = ReplayVerdict(
                external_id=t.external_id, source=t.source, title=t.title,
                passed_prefilter=v.passed, dropped_at_stage=v.dropped_at,
                matched_kw=v.matched_kw, uzex_bypass=v.uzex_bypass, is_lead=v.is_lead,
            )
            if not v.passed:
                rv.delivered = False
            elif v.uzex_bypass:
                # Annotate-not-gate: bypass rows are sent regardless of AI.
                rv.delivered = True
                rv.ai_skipped = True
            elif not use_ai:
                rv.delivered = None       # prefilter-only mode: AI outcome unknown
            elif v.is_lead:
                rv.ai_skipped = False
                try:
                    spam = await _ai_lead_is_spam(t, client)
                    rv.delivered = not spam
                except Exception:
                    rv.ai_error = True
                    rv.delivered = True   # prod lead gate fails open (keep)
            else:
                rv.ai_skipped = False
                try:
                    r = await _ai_check_relevance(t, client)
                    rv.ai_score = r.score
                    rv.ai_category = r.category
                    rv.ai_error = r.score is None
                    rv.delivered = r.is_relevant
                except Exception:
                    rv.ai_error = True
                    rv.delivered = True   # prod fail-open
            if rv.delivered:
                # In-memory only — prod persists here, replay must not.
                if rv.ai_score is not None:
                    t.relevance_score = rv.ai_score
                    t.relevance_category = rv.ai_category
                rv.route = "push" if _route_to_push(t, mutes) else "digest"
            verdicts.append(rv)
    finally:
        if client is not None:
            await client.aclose()
    return verdicts


def _fetch_one(external_id, source):
    # type: (str, str) -> Optional[dict]
    from crawler.core.db import _get_client, query_with_retry

    def _q():
        return (_get_client().table("tenders")
                .select("external_id,source,title,organization,search_text,price,currency,"
                        "deadline,message_type,extra_info,bid_count,status,collected_at")
                .eq("external_id", external_id).eq("source", source)
                .limit(1).execute())

    rows = query_with_retry(_q, label="replay-fetch").data or []
    return rows[0] if rows else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--external-id")
    ap.add_argument("--source")
    ap.add_argument("--from-json", help="JSON file with a list of tender rows")
    ap.add_argument("--ai", action="store_true", help="run the AI gate too (costs cents)")
    ap.add_argument("--as-of", default="collected_at",
                    help="'collected_at' (default) | 'now' | ISO date")
    a = ap.parse_args()

    rows = []
    if a.from_json:
        rows = json.load(open(a.from_json))
    elif a.external_id and a.source:
        row = _fetch_one(a.external_id, a.source)
        if not row:
            print("row not found: %s / %s" % (a.external_id, a.source))
            sys.exit(2)
        rows = [row]
    else:
        ap.print_help()
        sys.exit(2)

    tenders = [row_to_raw_tender(r) for r in rows]
    ts_map = dict((str(r.get("external_id")), r.get("collected_at"))
                  for r in rows if r.get("collected_at"))
    out = asyncio.run(replay_tenders(tenders, use_ai=a.ai, as_of=a.as_of,
                                     collected_at=ts_map))
    for v in out:
        stage = v.dropped_at_stage or ("bypass" if v.uzex_bypass else "passed")
        # Leads go through the spam gate, which is keep/drop and has NO score —
        # printing "ai=None" there reads like a failure when it is the design.
        if v.is_lead and not v.ai_skipped:
            ai = "lead-keep" if v.delivered else "lead-drop"
        elif v.ai_skipped:
            ai = "skipped"
        else:
            ai = "%s/%s" % (v.ai_score, v.ai_category)
        print("%-14s | %-9s | kw=%-12s | ai=%-14s%s | route=%s | %s" % (
            (v.external_id or "")[:14], stage, v.matched_kw, ai,
            " ERR" if v.ai_error else "", v.route, (v.title or "")[:60]))


if __name__ == "__main__":
    main()
