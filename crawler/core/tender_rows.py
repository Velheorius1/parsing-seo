"""Строка БД/площадки → RawTender. Одна реализация на все скрипты.

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ (05.09). Функция жила в `scripts/replay.py`, и её
импортировали `recheck` и `customer_audit`. Но `replay` при импорте выставляет
`PARSING_AI_LOG` (ему это нужно ДО импорта crawler-модулей), поэтому «взять
один маппинг» означало «утащить побочный эффект». `recall_audit` его не
утащил — и завёл свою урезанную копию без приведения типов, из-за чего ночной
аудит полноты падал на `extra_info.lots` (список там, где pydantic ждёт
строку). Замечено 05.09: сторож полноты молча не работал.
"""

from typing import Optional  # noqa: F401  (используется в аннотациях-комментариях)

from crawler.core.models import RawTender


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
