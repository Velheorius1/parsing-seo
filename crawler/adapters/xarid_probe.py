"""Старые аукционы UZEX (xarid.uzex.uz/auction): перебор лотов по номеру.

20.10.2026: независимая сверка нашла у этой системы 629 открытых лотов за
три недели — печать книг КГУ 283 млн, НУУз 127,6 млн, СамГМУ (бланки,
блокноты, бумажные пакеты), плакаты 132 и 75 млн, — и ни одного в нашей базе.
Источник «UZEX Обратные аукционы» читает ДРУГУЮ систему (auctionX, свои номера).

Список лотов здесь закрыт RSA-токеном, а лот по номеру отдаётся без входа:
GET Common/GetLot/{id}; «нет лота» — 500 с ORA-01403; короткий User-Agent —
500 «Missing User-Agent header» (нужен полный браузерный). Номера идут подряд.

Прогон: сначала очередь лотов, ещё не вышедших на торги («Проверка на
аффилированность», «Одобрен Казной»), потом вперёд от next_id до max_misses
«нет лота» подряд. В конвейер уходит только лот «Опубликован» — у него есть
срок; завершённые и снятые пропускаются.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from crawler.adapters.base import BaseAdapter
from crawler.core.models import RawTender

logger = logging.getLogger(__name__)

PUBLISHED = "опубликован"
# Лот ещё не на торгах — перепроверяем, пока не опубликуют или не снимут.
_PENDING_HINTS = ("провер", "одобрен", "модерац", "рассмотр", "соглас")
_NOT_FOUND = "ORA-01403"
_NO_DATE = "0001-01-01"


def _str(value):
    # type: (Any) -> str
    return " ".join(str(value or "").split())


def _date(value):
    # type: (Any) -> Optional[str]
    s = _str(value)
    return None if not s or s.startswith(_NO_DATE) else s


class XaridProbeAdapter(BaseAdapter):
    """adapter: xarid_probe — см. модуль и ProbeConfig."""

    async def _fetch_items(self):
        # type: () -> List[RawTender]
        from crawler.auth.session_store import session_store

        cfg = self.config
        pc = cfg.probe
        if pc is None:
            raise ValueError("probe config required (source: %s)" % cfg.id)
        state = None
        try:
            state = session_store.get_setting(pc.state_key)
        except Exception:
            pass
        state = state if isinstance(state, dict) else {}
        next_id = int(state.get("next_id") or pc.start_id)
        pending = dict((str(k), v) for k, v in (state.get("pending") or {}).items())
        now = datetime.now(timezone.utc)
        stale = (now - timedelta(days=pc.pending_days)).isoformat()
        for key in [k for k, since in pending.items() if str(since) < stale]:
            pending.pop(key)

        budget = pc.max_per_run
        out = []  # type: List[RawTender]
        found = missing = errors = 0
        start_next = next_id
        async with httpx.AsyncClient(timeout=httpx.Timeout(cfg.timeout, connect=10.0),
                                     headers=cfg.headers) as client:
            for key in sorted(pending, key=int):
                if budget <= 0:
                    break
                budget -= 1
                kind, lot = await self._get(client, int(key))
                if kind == "missing":
                    pending.pop(key, None)
                elif kind == "ok":
                    self._route(lot, key, pending, out, now)

            misses = 0
            errors_in_row = 0
            highest = next_id - 1
            cur = next_id
            while budget > 0 and misses < pc.max_misses:
                budget -= 1
                kind, lot = await self._get(client, cur)
                if kind == "error":
                    # Один сбойный номер не держит перебор (как detail 520753
                    # держал спецификации etender): номер — в очередь перепроверки.
                    errors += 1
                    errors_in_row += 1
                    pending.setdefault(str(cur), now.isoformat())
                    if errors_in_row >= 3:
                        break  # похоже на сеть или сервер: продолжим со следующего прогона
                    misses += 1
                elif kind == "missing":
                    errors_in_row = 0
                    misses += 1
                    missing += 1
                else:
                    errors_in_row = misses = 0
                    found += 1
                    highest = cur
                    self._route(lot, str(cur), pending, out, now)
                cur += 1
            next_id = highest + 1
            for key in [k for k in pending if int(k) >= next_id]:
                pending.pop(key)  # выше next_id их и так пройдёт перебор

        logger.info("[%s] probe %d..%d: %d lots, %d missing, %d errors; %d published, %d pending",
                    cfg.name, start_next, cur - 1, found, missing, errors, len(out), len(pending))
        try:
            session_store.set_setting(pc.state_key, {
                "next_id": next_id, "pending": pending, "updated_at": now.isoformat(),
            })
        except Exception as exc:
            logger.warning("[%s] probe state write failed: %s", cfg.name, str(exc)[:80])
        return out

    async def _get(self, client, lot_id):
        # type: (httpx.AsyncClient, int) -> Tuple[str, Optional[Dict[str, Any]]]
        """("ok", лот) | ("missing", None) | ("error", None)."""
        await self.rate_limit()
        try:
            resp = await client.get(self.config.url.replace("{id}", str(lot_id)))
        except Exception as exc:
            logger.warning("[%s] lot %s: %s", self.config.name, lot_id, str(exc)[:120])
            return "error", None
        if _NOT_FOUND in resp.text:
            return "missing", None
        if resp.status_code != 200:
            logger.warning("[%s] lot %s: HTTP %s %s", self.config.name, lot_id,
                           resp.status_code, resp.text[:120])
            return "error", None
        try:
            data = resp.json()
        except ValueError:
            return "error", None
        return ("ok", data) if isinstance(data, dict) and data.get("id") else ("error", None)

    def _route(self, lot, key, pending, out, now):
        # type: (Dict[str, Any], str, Dict[str, Any], List[RawTender], datetime) -> None
        status = _str(lot.get("status_name")).lower()
        if status.startswith(PUBLISHED):
            pending.pop(key, None)
            out.append(self._to_tender(lot))
        elif any(h in status for h in _PENDING_HINTS):
            pending.setdefault(key, now.isoformat())
        else:
            pending.pop(key, None)  # совершён, отказан, удалён — не наше дело

    def _to_tender(self, lot):
        # type: (Dict[str, Any]) -> RawTender
        cfg = self.config
        lot_id = str(lot.get("id"))
        details = [d for d in (lot.get("js_details") or []) if isinstance(d, dict)]
        names = []  # type: List[str]
        for d in details:
            name = _str(d.get("product_name"))
            if name and name not in names:
                names.append(name)
        category = _str(lot.get("category_name"))
        title = category + (": " + "; ".join(names[:3]) if names else "")
        description = _str(lot.get("description"))
        organization = _str(lot.get("customer_name"))
        tpl = cfg.field_map.source_url_template or "https://xarid.uzex.uz/auction/detail/{id}"
        end = _date(lot.get("end_date"))
        extra = {"Номер": _str(lot.get("display_no")), "Позиций": str(len(details))}
        if description:
            extra["Описание"] = description[:300]
        if lot.get("district_name"):
            extra["Район"] = _str(lot.get("district_name"))
        if lot.get("delivery_days"):
            extra["Срок поставки, дн"] = str(lot.get("delivery_days"))
        return RawTender(
            id="%s-%s" % (cfg.id_prefix, lot_id), external_id=lot_id,
            title=title[:300], organization=organization,
            price=lot.get("start_cost"), currency="UZS",
            deadline=end, date_start=_date(lot.get("start_date")), date_end=end,
            region=_str(lot.get("region_name")), source=cfg.name,
            source_url=tpl.replace("{id}", lot_id),
            # Ищем по категории и названиям ВСЕХ позиций. Описания лота и позиций —
            # только в карточку: там требования к поставке («в коробке», «qadoq»),
            # и на 627 открытых лотах 20.10 словарь пропускал 135 вместо 42 — все
            # книги и плакаты из сверки остаются и без описаний.
            search_text=" ".join([category] + names + [organization])[:2000],
            bid_count=lot.get("pcp_count"), extra_info=extra,
        )
