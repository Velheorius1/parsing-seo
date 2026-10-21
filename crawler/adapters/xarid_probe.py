"""Старые аукционы UZEX (xarid.uzex.uz/auction): перебор лотов по номеру.

20.10.2026: независимая сверка нашла у этой системы 629 открытых лотов за
три недели — печать книг КГУ 283 млн, НУУз 127,6 млн, СамГМУ (бланки,
блокноты, бумажные пакеты), плакаты 132 и 75 млн, — и ни одного в нашей базе.
Источник «UZEX Обратные аукционы» читает ДРУГУЮ систему (auctionX, свои номера).

Лот по номеру отдаётся без входа: GET Common/GetLot/{id}; «нет лота» — 500 с
ORA-01403; короткий User-Agent — 500 «Missing User-Agent header» (нужен полный
браузерный). Номера идут подряд.

Основной путь (21.10): список активных лотов `list_url` открыт (окно 20, сорт.
по сроку), подробности качаются только для номеров, которых ещё нет в памяти
`listed`; закрытый лот уходит из списка и из памяти. 20.10 считалось, что список
закрыт токеном (так было 11.06) — перебор со start_id потерял лот 417947.

Запасной путь — список не ответил (ошибка пишется в crawl_runs): сначала очередь
лотов, ещё не вышедших на торги («Проверка на аффилированность», «Одобрен
Казной»), потом вперёд от next_id до max_misses «нет лота» подряд. В конвейер
уходит только лот «Опубликован» — у него есть срок; завершённые и снятые
пропускаются.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from crawler.adapters.api import _safe_str
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
    """_safe_str + схлопнутые пробелы: статусы приходят как «Одобрен Казной\\n»."""
    return " ".join(_safe_str(value).split())


def _due(entry, now):
    # type: (Dict[str, str], datetime) -> bool
    """Пора ли перепроверить лот из очереди (ревью 20.10: 300–400 лотов в очереди
    каждые 2 часа съедали бюджет перебора). Свежий — каждый прогон, 1–3 дня —
    раз в 6 часов, старше — раз в 12."""
    try:
        since = datetime.fromisoformat(entry.get("since") or "")
        checked = datetime.fromisoformat(entry["checked"]) if entry.get("checked") else None
    except ValueError:
        return True
    age = now - since
    every = timedelta(0) if age < timedelta(days=1) else (
        timedelta(hours=6) if age < timedelta(days=3) else timedelta(hours=12))
    return checked is None or now - checked >= every


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
        now = datetime.now(timezone.utc)
        pending = {}  # type: Dict[str, Dict[str, str]]
        for key, value in (state.get("pending") or {}).items():
            if isinstance(value, dict):
                pending[str(key)] = {"since": str(value.get("since") or now.isoformat()),
                                     "checked": str(value.get("checked") or "")}
            else:  # формат до 21.10: id -> since
                pending[str(key)] = {"since": str(value), "checked": ""}
        stale = (now - timedelta(days=pc.pending_days)).isoformat()
        for key in [k for k, entry in pending.items() if entry["since"] < stale]:
            pending.pop(key)

        # Опубликованные прошлым прогоном отдаём ещё раз (ревью 20.10): state
        # сохраняется ДО записи в БД, и при сбое записи лот иначе терялся бы
        # навсегда — перебор ушёл дальше. Повтор идемпотентен: строка уже есть,
        # алерта второй раз не будет.
        out = []  # type: List[RawTender]
        for data in state.get("last_published") or []:
            try:
                out.append(RawTender(**data))
            except Exception:
                continue
        replayed = len(out)

        budget = pc.max_per_run
        found = missing = errors = 0
        start_next = next_id
        listed = set(str(k) for k in state.get("listed") or [])
        async with httpx.AsyncClient(timeout=httpx.Timeout(cfg.timeout, connect=10.0),
                                     headers=cfg.headers) as client:
            active = None  # type: Optional[List[int]]
            if pc.list_url:
                active, list_error = await self._list(client)
                if active is None:
                    self.last_error = "list failed (%s), probe fallback" % list_error
                    logger.warning("[%s] %s", cfg.name, self.last_error)
            if active is not None:
                listed &= set(str(k) for k in active)  # закрытые ушли из списка
                errors_in_row = 0
                for key in [str(k) for k in sorted(set(active)) if str(k) not in listed]:
                    if budget <= 0:
                        break
                    budget -= 1
                    kind, lot = await self._get(client, int(key))
                    if kind == "ok":
                        errors_in_row = 0
                        found += 1
                        if self._route(lot, key, pending, out, now):
                            listed.add(key)
                    elif kind == "missing":
                        missing += 1
                    else:
                        errors += 1
                        errors_in_row += 1
                        if errors_in_row >= 3:
                            self.last_error = "list: lot %s: %d errors in a row" % (key, errors_in_row)
                            break
                # До публикации лота в списке нет — очередь не нужна; перебор
                # (если список закроют) начнёт с края списка, а не с прошлого места.
                pending = {}
                next_id = max(next_id, max(active) + 1)
                cur = next_id
            for key in sorted(pending, key=int) if active is None else []:
                if budget <= 0:
                    break
                if not _due(pending[key], now):
                    continue
                budget -= 1
                pending[key]["checked"] = now.isoformat()
                kind, lot = await self._get(client, int(key))
                if kind == "missing":
                    pending.pop(key, None)
                elif kind == "ok":
                    self._route(lot, key, pending, out, now)

            misses = 0
            errors_in_row = 0
            highest = next_id - 1
            cur = next_id
            while active is None and budget > 0 and misses < pc.max_misses:
                budget -= 1
                kind, lot = await self._get(client, cur)
                if kind == "error":
                    # Один сбойный номер не держит перебор (как detail 520753
                    # держал спецификации etender): номер — в очередь перепроверки.
                    errors += 1
                    errors_in_row += 1
                    pending.setdefault(str(cur), {"since": now.isoformat(), "checked": now.isoformat()})
                    if errors_in_row >= 3:
                        # Похоже на сеть или сервер: продолжим со следующего прогона,
                        # а сбой — в crawl_runs, иначе источник молча встанет (ревью 20.10).
                        self.last_error = "probe stopped at %d: %d errors in a row" % (cur, errors_in_row)
                        break
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
            if active is None:
                next_id = highest + 1
                for key in [k for k in pending if int(k) >= next_id]:
                    pending.pop(key)  # выше next_id их и так пройдёт перебор

        fresh = out[replayed:]
        if active is not None:
            logger.info("[%s] list: %d active, %d fetched, %d missing, %d errors; %d published (+%d replayed)",
                        cfg.name, len(set(active)), found, missing, errors, len(fresh), replayed)
        else:
            logger.info("[%s] probe %d..%d: %d lots, %d missing, %d errors; %d published (+%d replayed), %d pending",
                        cfg.name, start_next, cur - 1, found, missing, errors, len(fresh), replayed, len(pending))
        try:
            session_store.set_setting(pc.state_key, {
                "next_id": next_id, "pending": pending, "updated_at": now.isoformat(),
                "listed": sorted(listed, key=int),
                "last_published": [t.model_dump(mode="json", exclude={"collected_at"}) for t in fresh],
            })
        except Exception as exc:
            logger.warning("[%s] probe state write failed: %s", cfg.name, str(exc)[:80])
        return out

    async def _list(self, client):
        # type: (httpx.AsyncClient) -> Tuple[Optional[List[int]], str]
        """Номера всех активных лотов из списка | (None, причина).

        Окно двигается по списку, отсортированному по сроку: лот, закрывшийся
        посреди листания, может сдвинуть соседа через границу окна — такой
        поймает следующий прогон. Пустой список — тоже сбой: активных лотов
        здесь всегда сотни, а пусто бывает, когда снова требуют токен.
        """
        pc = self.config.probe
        ids = []  # type: List[int]
        start, total = 1, None
        while total is None or start <= total:
            await self.rate_limit()
            try:
                resp = await client.post(pc.list_url, json={"from": start, "to": start + pc.list_window - 1})
            except Exception as exc:
                return None, "from %d: %s" % (start, str(exc)[:80])
            if resp.status_code != 200:
                return None, "from %d: HTTP %s" % (start, resp.status_code)
            try:
                page = resp.json()
            except ValueError:
                return None, "from %d: not json" % start
            if not isinstance(page, list):
                return None, "from %d: %s" % (start, type(page).__name__)
            if not page:
                break
            ids.extend(int(row["id"]) for row in page if isinstance(row, dict) and row.get("id"))
            try:
                total = int(page[0].get("total_count") or 0)
            except (TypeError, ValueError):
                return None, "from %d: no total_count" % start
            start += pc.list_window
        if not ids:
            return None, "empty"
        return ids, ""

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
        # type: (Dict[str, Any], str, Dict[str, Any], List[RawTender], datetime) -> bool
        """True — лот опубликован и ушёл в конвейер."""
        status = _str(lot.get("status_name")).lower()
        if status.startswith(PUBLISHED):
            pending.pop(key, None)
            out.append(self._to_tender(lot))
            return True
        if any(h in status for h in _PENDING_HINTS):
            pending.setdefault(key, {"since": now.isoformat(), "checked": now.isoformat()})
        else:
            pending.pop(key, None)  # совершён, отказан, удалён — не наше дело
        return False

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
