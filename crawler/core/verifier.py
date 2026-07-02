"""Pre-send live verification of push alerts (V1, design 2026-07-02).

Kills the «алерт есть — тендера нет» class: right before the Telegram send loop,
each PUSH candidate is re-checked against its source platform (does the lot still
exist / is it still open). Confirmed closed/gone lots are dropped with a log line;
anything uncertain FAILS OPEN (sent as before) — a verifier bug must never
silently eat a winnable order (Anthropic guardrail; mirrors the AI _allow()
discipline).

Deterministic by design — no LLM on this path («Building effective agents»:
workflows over agents for predictable steps). Per-source detail endpoints:
  - birja (XT-Xarid / Hayotbirja, shared backend): anonymous `get_proc` urpc —
    probe-verified 2026-07-02 (docs/checkpoints/xtx-transition-probe.md):
    status "publicated/open" = live, "close" = ended, 7-key stub w/o status =
    purged → unverifiable, RPC error → unverifiable (fail-open).
  - ETender UZEX: GET apietender.uzex.uz/api/common/GetTrade/{id}/0 (the P7
    detail endpoint) — 200+body = exists, 404 = gone.
  - UZEX Предквалификации: GET xarid-api-prequest.uzex.uz/api/Public/GetLot?id=
    (route verified 2026-06-21) via UZ residential proxy (geo-blocked otherwise).
  - Everything else (TG leads, banks, SPA, cooperation): unverifiable → send.

Digest items are not verified (non-urgent; cost).
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import httpx

from crawler.config.settings import settings
from crawler.core.models import RawTender

logger = logging.getLogger(__name__)

OK = "ok"
CLOSED = "closed"
GONE = "gone"
UNVERIFIABLE = "unverifiable"

_OPEN_STATUSES = {"open", "publicated", "published", "active"}
_CLOSED_STATUSES = {"close", "closed", "cancel", "cancelled", "not_realized",
                    "finished", "completed", "ended"}

_VERIFY_TIMEOUT = 12  # seconds per lot (wait_for)
_CONCURRENCY = 5


@dataclass
class VerifyResult:
    status: str
    reason: str = ""


def _birja_base(source: str) -> Optional[str]:
    s = source or ""
    if s.startswith("XT-Xarid"):
        return "https://api.xt-xarid.uz"
    if s.startswith("Hayotbirja"):
        return "https://api.hayotbirja.uz"
    return None


async def _verify_birja(t: RawTender, client: httpx.AsyncClient) -> VerifyResult:
    base = _birja_base(t.source)
    try:
        proc_id = int(str(t.external_id).strip())
    except (TypeError, ValueError):
        return VerifyResult(UNVERIFIABLE, "non-numeric id")
    resp = await client.post(
        base + "/urpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "get_proc",
              "params": {"proc_id": proc_id}},
    )
    # urpc returns RPC-level errors as HTTP 400 WITH a JSON body — parse it,
    # don't raise. Only 5xx is a real infra failure.
    if resp.status_code >= 500:
        return VerifyResult(UNVERIFIABLE, "http %d" % resp.status_code)
    try:
        j = resp.json()
    except ValueError:
        return VerifyResult(UNVERIFIABLE, "non-json (%d)" % resp.status_code)
    if "error" in j and j.get("error"):
        # Probe showed nonexistent id -> RPC error; but a transient server error
        # looks the same shape. Fail open (recall > precision here).
        return VerifyResult(UNVERIFIABLE, "rpc error")
    result = j.get("result")
    if not isinstance(result, dict):
        return VerifyResult(UNVERIFIABLE, "no result")
    status = str(result.get("status") or "").lower()
    if status in _CLOSED_STATUSES:
        return VerifyResult(CLOSED, "get_proc status=%s" % status)
    if status in _OPEN_STATUSES:
        return VerifyResult(OK, "get_proc status=%s" % status)
    # 7-key stub with no status = old/purged lot — uncertain, fail open.
    return VerifyResult(UNVERIFIABLE, "status=%r keys=%d" % (status, len(result)))


async def _verify_etender(t: RawTender, client: httpx.AsyncClient) -> VerifyResult:
    # ETender external_id in DB is the LONG display number (26121006496165);
    # GetTrade expects the SHORT lot id — it's the /lot/{id} slug in source_url.
    import re as _re
    m = _re.search(r"/lot/(\d+)", t.source_url or "")
    if not m:
        return VerifyResult(UNVERIFIABLE, "no /lot/ id in url")
    lot_id = int(m.group(1))
    resp = await client.get(
        "https://apietender.uzex.uz/api/common/GetTrade/%d/0" % lot_id)
    if resp.status_code == 404:
        return VerifyResult(GONE, "GetTrade 404")
    if resp.status_code != 200:
        return VerifyResult(UNVERIFIABLE, "GetTrade %d" % resp.status_code)
    try:
        body = resp.json()
    except ValueError:
        return VerifyResult(UNVERIFIABLE, "non-json")
    if not body:
        return VerifyResult(GONE, "GetTrade empty body")
    return VerifyResult(OK, "GetTrade 200")


async def _verify_prequest(t: RawTender, client: httpx.AsyncClient) -> VerifyResult:
    # xarid-api-* is UZ geo-blocked from the RU VPS — must go through the
    # residential proxy (same as the listing crawl; sources.yaml use_proxy).
    proxy = settings.residential_proxy_url
    if not proxy:
        return VerifyResult(UNVERIFIABLE, "no proxy")
    try:
        lot_id = int(str(t.external_id).strip())
    except (TypeError, ValueError):
        return VerifyResult(UNVERIFIABLE, "non-numeric id")
    async with httpx.AsyncClient(timeout=10, proxy=proxy) as pclient:
        resp = await pclient.get(
            "https://xarid-api-prequest.uzex.uz/api/Public/GetLot",
            params={"id": lot_id})
    if resp.status_code == 404:
        return VerifyResult(GONE, "GetLot 404")
    if resp.status_code != 200:
        return VerifyResult(UNVERIFIABLE, "GetLot %d" % resp.status_code)
    try:
        body = resp.json()
    except ValueError:
        return VerifyResult(UNVERIFIABLE, "non-json")
    if not body:
        return VerifyResult(GONE, "GetLot empty")
    return VerifyResult(OK, "GetLot 200")


async def verify_lot(t: RawTender, client: httpx.AsyncClient) -> VerifyResult:
    """Dispatch to the per-source verifier. Unknown source → unverifiable."""
    src = t.source or ""
    if _birja_base(src):
        return await _verify_birja(t, client)
    if src.startswith("ETender"):
        return await _verify_etender(t, client)
    if src == "UZEX Предквалификации":
        return await _verify_prequest(t, client)
    return VerifyResult(UNVERIFIABLE, "no detail API")


async def verify_push_batch(
    matching: List[Tuple[RawTender, str]],
) -> List[Tuple[RawTender, str]]:
    """Verify push candidates concurrently; drop confirmed closed/gone.

    Fail-open on ANY exception/timeout — never silently drop on infrastructure
    hiccups. Returns the filtered (tender, keyword) list.
    """
    if not matching:
        return matching
    sem = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(timeout=10) as client:
        async def _one(tk):
            t, _kw = tk
            try:
                async with sem:
                    r = await asyncio.wait_for(
                        verify_lot(t, client), timeout=_VERIFY_TIMEOUT)
            except Exception as exc:
                r = VerifyResult(UNVERIFIABLE, str(exc)[:60])
            return tk, r

        results = await asyncio.gather(*[_one(tk) for tk in matching])

    kept = []
    dropped = 0
    for (t, kw), r in results:
        if r.status in (CLOSED, GONE):
            dropped += 1
            logger.info("[Verify] DROPPED (%s): %s | %s | %s",
                        r.status, (t.title or "")[:44], t.source[:28], r.reason)
            continue
        if r.status == OK:
            try:
                t.extra_info["Проверено"] = "✅ лот активен на площадке"
            except Exception:
                pass
        kept.append((t, kw))
    if dropped:
        logger.info("[Verify] %d/%d push candidates dropped as stale", dropped, len(matching))
    return kept
