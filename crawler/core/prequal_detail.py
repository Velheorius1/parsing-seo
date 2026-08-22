"""Предмет лота у предквалификаций UZEX — дотянуть ДО гейтов.

Из чего выросло (22.08). Данияр прокликал алерты дороже 15 млн, и два самых
дорогих оказались мимо по одной причине:

  #7751, 349 млн — заголовок «Услуги издательские», предмет «Услуга публикации
                   статьи» (размещение в СМИ);
  #7737,  24 млн — заголовок «Услуги печатные и услуги по копированию…»,
                   предмет «Услуга по установке баннера» ×2.

Виноват не AI. В `sources.yaml` у источника стоит `title: categoryName`, а
список `GetLots` отдаёт ТОЛЬКО категорию — предмета в нём нет вовсе. В базе:

    search_text #7751 = "Услуги издательские \\"O`ZBEKTELEKOM\\" AJ"

То есть модель оценивала название рубрики вслепую и ставила 90-95. Мусор на
входе — мусор на выходе.

ПОЧЕМУ ДО ГЕЙТОВ, А НЕ ПОСЛЕ. Скрипт `uzex_prequal_enricher.py` умеет тянуть
детали с 2026-07, но работает по базе и ПОСЛЕ upsert — к моменту его запуска
AI уже вынес вердикт. Вдобавок в крон его так и не поставили: 13 911 строк из
79 323 обогащены разовым ручным прогоном, дальше ноль.

Дыра при этом ДВУСТОРОННЯЯ, и вторая сторона дороже. Без предмета не только
чужой лот проходит гейт — НАШ лот его не проходит: если категория непрофильная
(«Услуги общественных организаций»), а предмет «печать буклетов», ключевой
гейт отсечёт лот ДО AI, и мы его никогда не увидим. Поэтому обогащение стоит
перед `send_alerts`, а не внутри него после префильтра.

ОТКАЗ — БЕЗ ПОТЕРЬ. Если деталь не пришла, `search_text` остаётся прежним:
пустой search_text хуже категории, модель увидела бы вообще ничего.
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

PREQUAL_SOURCE = "UZEX Предквалификации"
_API_URL = "https://xarid-api-prequest.uzex.uz/api/Public/GetLot"

# Предметы дописываются в search_text, а он уезжает в промпт. В `_ai_call_one`
# из него берутся первые 320 знаков — больше просто не доедет, а платить за
# токены смысла нет.
_MAX_POSITIONS_CHARS = 300

_DIGITS = re.compile(r"(\d+)")


def lot_id(external_id):
    # type: (Any) -> Optional[str]
    """Числовой id лота из external_id. `uzex-prq-100486` -> `100486`.

    Префикс мог протечь в id (так уже бывало у других источников), поэтому
    берём ПОСЛЕДНЮЮ группу цифр, а не первую: в `uzex-prq-100486` первая — это
    ничто, а в чистом `100486` последняя и есть ответ.
    """
    if external_id is None:
        return None
    found = _DIGITS.findall(str(external_id))
    return found[-1] if found else None


def positions_from_detail(data):
    # type: (Any) -> List[str]
    """Названия позиций лота, по порядку и без повторов.

    Повторы реальны: у лота 100279 «Услуга по установке баннера» стоит дважды
    (две позиции разного объёма). Для модели это одно и то же слово дважды —
    только шум и лишние токены.
    """
    if not isinstance(data, dict):
        return []
    out = []  # type: List[str]
    seen = set()
    for item in (data.get("details") or []):
        if not isinstance(item, dict):
            continue
        name = item.get("productName") or item.get("name") or ""
        name = " ".join(str(name).split())
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def merged_search_text(existing, positions):
    # type: (Optional[str], List[str]) -> Optional[str]
    """Категория + предметы. None, если дописывать нечего.

    Идемпотентно: повторный прогон не задваивает текст — предмет, который уже
    внутри, не дописывается. Иначе ночные перепрогоны раздували бы строку и
    выталкивали полезное за срез в 320 знаков.
    """
    if not positions:
        return None
    base = (existing or "").strip()
    low = base.lower()
    fresh = [p for p in positions if p.lower() not in low]
    if not fresh:
        return None
    tail = " · ".join(fresh)[:_MAX_POSITIONS_CHARS]
    return ("%s | %s" % (base, tail)).strip(" |")


async def _fetch_detail(client, lid):
    # type: (Any, str) -> Optional[Dict[str, Any]]
    try:
        resp = await client.get(_API_URL, params={"id": lid}, timeout=15)
        if resp.status_code != 200:
            return None
        body = resp.json()
        if body.get("Status") != 200:
            return None
        data = body.get("Data")
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug("[Prequal] %s: %s", lid, str(exc)[:120])
        return None


async def enrich(tenders, dry_run=False, max_lots=200):
    # type: (List[Any], bool, int) -> int
    """Дотянуть предметы для предквалификаций. Возвращает число обогащённых.

    Трогает ТОЛЬКО `PREQUAL_SOURCE`. Чужие источники не задеваются даже при
    ошибке: фильтр стоит первым и по точному имени.
    """
    targets = [t for t in (tenders or []) if getattr(t, "source", None) == PREQUAL_SOURCE]
    if not targets:
        return 0
    if len(targets) > max_lots:
        logger.warning("[Prequal] %d лотов, беру первые %d — остальные без предмета",
                       len(targets), max_lots)
        targets = targets[:max_lots]

    import httpx

    enriched = 0
    async with httpx.AsyncClient(
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
        timeout=20, trust_env=False,
    ) as client:
        for t in targets:
            lid = lot_id(getattr(t, "external_id", None))
            if not lid:
                continue
            data = await _fetch_detail(client, lid)
            if not data:
                continue
            positions = positions_from_detail(data)
            merged = merged_search_text(getattr(t, "search_text", None), positions)
            if not merged:
                continue
            t.search_text = merged
            extra = dict(getattr(t, "extra_info", None) or {})
            extra["lots"] = data.get("details") or []
            t.extra_info = extra
            enriched += 1

    if enriched and not dry_run:
        _persist(targets)
    logger.info("[Prequal] предмет дотянут у %d из %d лотов%s",
                enriched, len(targets), " (dry-run)" if dry_run else "")
    return enriched


def _persist(tenders):
    # type: (List[Any]) -> None
    """Записать обогащённое в базу, чтобы replay и фронт видели то же самое.

    Сбой записи не роняет краул: в памяти лоты уже обогащены, а гейты идут
    дальше по памяти. Потерять алерт из-за неудачного UPDATE было бы хуже.
    """
    try:
        from crawler.core.db import _get_client
        client = _get_client()
    except Exception as exc:
        logger.warning("[Prequal] нет клиента БД: %s", str(exc)[:120])
        return
    for t in tenders:
        st = getattr(t, "search_text", None)
        if not st:
            continue
        try:
            client.table("tenders").update(
                {"search_text": st, "extra_info": getattr(t, "extra_info", None) or {}}
            ).eq("external_id", t.external_id).eq("source", PREQUAL_SOURCE).execute()
        except Exception as exc:
            logger.warning("[Prequal] update %s: %s", t.external_id, str(exc)[:120])
