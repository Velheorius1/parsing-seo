"""Чистые правила для исследовательского аудита побед конкурентов.

Модуль не делает HTTP-запросов, не читает настройки production и не пишет в БД.
Идентичность поставщика устанавливается только точным ИНН. Названия в реестре
предназначены для ручного поиска и не участвуют в автоматическом объединении сумм.
"""
from __future__ import annotations

import json
import os
import re
import hashlib
from decimal import Decimal, InvalidOperation
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Set


THRESHOLD_UZS = Decimal("20000000")
_INN_RE = re.compile(r"^[0-9]{9}$")
_UZS_CURRENCIES = frozenset(("uzs", "сум", "сом", "sum", "so'm", "soʻm", "сўм"))


def registry_path() -> str:
    """Возвращает путь к versioned registry без зависимости от текущего cwd."""
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "config",
                                         "competitor_registry.json"))


def normalize_inn(value: Any) -> Optional[str]:
    """Нормализует допустимый девятизначный ИНН, иначе возвращает unknown.

    Строки вроде ``0``, имени компании или повреждённого поля API не являются
    отрицательным совпадением — они не могут исключить компанию из аудита.
    """
    if value is None or isinstance(value, bool):
        return None
    normalized = str(value).strip()
    if not _INN_RE.match(normalized) or set(normalized) == {"0"}:
        return None
    return normalized


def normalize_currency(value: Any) -> Optional[str]:
    """Сводит известные написания узбекского сума к ``UZS``."""
    if value is None:
        return None
    normalized = str(value).strip().casefold().replace("’", "'")
    if normalized in _UZS_CURRENCIES:
        return "UZS"
    return None


def above_threshold(amount: Any, currency: Any) -> Optional[bool]:
    """Проверяет согласованный порог на итоговую сумму одной закупки.

    ``None`` означает unknown: сумму или валюту нельзя честно сопоставить с UZS.
    Ровно 20 млн не проходит.
    """
    if amount is None or normalize_currency(currency) != "UZS":
        return None
    try:
        return Decimal(str(amount)) > THRESHOLD_UZS
    except (InvalidOperation, ValueError, TypeError):
        return None


def _validate_entity(entity: Dict[str, Any], seen_inns: set) -> None:
    name = entity.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Competitor entity requires a non-empty name")
    inn = entity.get("inn")
    if inn is None:
        return
    normalized = normalize_inn(inn)
    if normalized is None:
        raise ValueError("Invalid registry INN for %s" % name)
    if normalized in seen_inns:
        raise ValueError("Duplicate registry INN %s" % normalized)
    seen_inns.add(normalized)
    entity["inn"] = normalized


def load_registry(path: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Загружает и валидирует реестр, сохраняя primary и candidate раздельно."""
    with open(path or registry_path(), encoding="utf-8") as handle:
        registry = json.load(handle)
    if not isinstance(registry, dict):
        raise ValueError("Competitor registry must be an object")
    seen_inns = set()
    for section in ("entities", "separate_candidates"):
        entries = registry.get(section)
        if not isinstance(entries, list):
            raise ValueError("Competitor registry section %s must be a list" % section)
        for entity in entries:
            if not isinstance(entity, dict):
                raise ValueError("Competitor registry entries must be objects")
            _validate_entity(entity, seen_inns)
    return registry


def entity_for_inn(registry: Dict[str, List[Dict[str, Any]]], inn: Any) -> Optional[Dict[str, Any]]:
    """Находит единственную сущность по точному ИНН; alias-name не используется."""
    wanted = normalize_inn(inn)
    if wanted is None:
        return None
    for section in ("entities", "separate_candidates"):
        for entity in registry.get(section, []):
            if entity.get("inn") == wanted:
                return entity
    return None


def registry_inns(registry: Dict[str, List[Dict[str, Any]]], include_candidates: bool = True) -> List[str]:
    """Возвращает ИНН для client-side join без fuzzy-сопоставления названий."""
    sections = ["entities"]
    if include_candidates:
        sections.append("separate_candidates")
    return [entity["inn"] for section in sections for entity in registry.get(section, [])
            if normalize_inn(entity.get("inn")) is not None]


def normalize_name(value: Any) -> str:
    """Conservative comparison form for public registries that omit INN."""
    return "".join(char for char in str(value or "").casefold() if char.isalnum())


def name_candidates(registry: Dict[str, List[Dict[str, Any]]], supplier_name: Any) -> List[Dict[str, Any]]:
    """Return review candidates, never an identity or economic aggregation."""
    supplier = normalize_name(supplier_name)
    if not supplier:
        return []
    matches = []
    for section in ("entities", "separate_candidates"):
        for entity in registry.get(section, []):
            aliases = [entity.get("name") or ""] + list(entity.get("input_names") or [])
            matching_aliases = []
            for alias in aliases:
                normalized = normalize_name(alias)
                if len(normalized) >= 6 and normalized in supplier:
                    matching_aliases.append(alias)
            if matching_aliases:
                matches.append({"entity": entity, "section": section, "aliases": matching_aliases})
    return matches


def page_body(source: str, page_index: int, page_size: int = 500) -> Dict[str, Any]:
    """Строит тело запроса, не скрывая различия пагинации публичных API."""
    if page_index < 0 or page_size <= 0:
        raise ValueError("page_index must be non-negative and page_size positive")
    start = 1 + page_index * page_size
    if source == "deals":
        # DealsList: верхняя граница exclusive, соседняя страница начинается с To.
        return {"From": start, "To": start + page_size, "System_Id": 0}
    if source in ("direct", "civil"):
        # Эти выдачи могут пересекаться на границе; collector дедуплицирует raw rows.
        return {"from": start, "to": start + page_size - 1}
    raise ValueError("Unsupported paginated source: %s" % source)


def _business_id(source: str, row: Dict[str, Any], fallback: str) -> str:
    fields = {
        "deals": ("deal_id", "trade_id", "display_no"),
        "direct": ("id", "display_id"),
        "civil": ("civil_contract_id", "civil_deal_id", "display_id"),
        "coop": ("id", "contract_id", "number"),
    }.get(source, ("id", "external_id", "display_id"))
    for field in fields:
        value = row.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "row:%s" % fallback


def _row_fingerprint(row: Dict[str, Any]) -> str:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def collect_pages(fetch_page: Callable[[int], List[Dict[str, Any]]], source: str,
                  max_pages: int, target_inns: Set[str]) -> Dict[str, Any]:
    """Собирает один источник и возвращает честный manifest без внешних записей.

    ``complete`` становится true только после пустой следующей страницы. Ошибка,
    повтор страницы и page cap сохраняют уже полученные записи, но не имитируют
    результат «0 побед» и не могут продвинуть watermark weekly-режима.
    """
    if max_pages <= 0:
        raise ValueError("max_pages must be positive")
    normalized_targets = {inn for inn in (normalize_inn(value) for value in target_inns)
                          if inn is not None}
    seen_rows = set()
    seen_business_ids = []
    seen_business_id_set = set()
    matches = []
    receipts = []
    completion = "page_cap"
    error = None

    for page_index in range(max_pages):
        try:
            rows = fetch_page(page_index)
        except Exception as exc:
            completion = "error"
            error = str(exc)
            receipts.append({"page": page_index + 1, "error": error})
            break
        if not isinstance(rows, list):
            completion = "schema_error"
            error = "expected list, got %s" % type(rows).__name__
            receipts.append({"page": page_index + 1, "error": error})
            break
        if not rows:
            receipts.append({"page": page_index + 1, "rows": 0, "new_rows": 0})
            completion = "archive_end"
            break

        new_rows = 0
        for row in rows:
            if not isinstance(row, dict):
                completion = "schema_error"
                error = "row is %s" % type(row).__name__
                break
            fingerprint = _row_fingerprint(row)
            if fingerprint in seen_rows:
                continue
            seen_rows.add(fingerprint)
            new_rows += 1
            business_id = _business_id(source, row, fingerprint)
            if business_id not in seen_business_id_set:
                seen_business_id_set.add(business_id)
                seen_business_ids.append(business_id)
            if normalize_inn(row.get("provider_inn")) in normalized_targets:
                matches.append(row)
        receipts.append({"page": page_index + 1, "rows": len(rows), "new_rows": new_rows})
        if completion == "schema_error":
            break
        if new_rows == 0:
            completion = "repeated_page"
            break

    return {
        "source": source,
        "completion": completion,
        "complete": completion == "archive_end",
        "error": error,
        "page_count": len(receipts),
        "unique_row_count": len(seen_rows),
        "unique_business_id_count": len(seen_business_ids),
        "unique_business_ids": seen_business_ids,
        "matches": matches,
        "receipts": receipts,
    }


def _decimal_text(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    text = format(decimal_value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _deal_status(raw_status: str) -> str:
    status = raw_status.casefold()
    if "отклон" in status:
        return "rejected"
    if "қабул" in status or "принят" in status or "амалга ошган" in status:
        return "accepted"
    if "протокол" in status:
        return "protocol_only"
    return "unknown"


def normalize_award(source: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """Преобразует одну запись API в доказательную модель результата.

    Не делает предположений: стартовая сумма остаётся отдельным полем, а прямой
    договор не становится открытым конкурсом. Неизвестный статус/сумма остаётся
    unknown для последующего ручного или документарного подтверждения.
    """
    if source == "deals":
        procedure_id = str(row.get("trade_id") or "") or None
        award_id = str(row.get("deal_id") or "") or None
        final_total = _decimal_text(row.get("deal_cost"))
        start_total = _decimal_text(row.get("start_cost"))
        raw_status = str(row.get("deal_status_name") or row.get("deal_contract_status_name") or
                         row.get("proposal_status_name") or "")
        status = _deal_status(raw_status)
        procedure_type = "etender_deal"
        evidence_url = ("https://etender.uzex.uz/lot/%s" % procedure_id) if procedure_id else None
        date_basis, awarded_at = "deal_date", row.get("deal_date")
        is_open_competition = True
    elif source == "direct":
        procedure_id = str(row.get("id") or "") or None
        award_id = procedure_id
        final_total = _decimal_text(row.get("contract_sum"))
        start_total = None
        raw_status = str(row.get("status_name") or "")
        status = "contract_published" if "опублик" in raw_status.casefold() else "unknown"
        procedure_type = "xarid_direct_contract"
        evidence_url = ("https://xarid-api-purchase.uzex.uz/Common/GetDirectPurchase/%s" % procedure_id
                        if procedure_id else None)
        date_basis, awarded_at = "contract_date", row.get("contract_date")
        is_open_competition = False
    elif source == "civil":
        procedure_id = str(row.get("civil_deal_id") or row.get("deal_num") or "") or None
        award_id = str(row.get("civil_contract_id") or "") or None
        final_total = _decimal_text(row.get("result_cost"))
        start_total = _decimal_text(row.get("cost"))
        raw_status = str(row.get("status_name") or "")
        status = _deal_status(raw_status) if raw_status else "resulted_unconfirmed"
        procedure_type = "etender_civil_contract"
        evidence_url = None
        date_basis, awarded_at = "deal_date", row.get("deal_date")
        is_open_competition = None
    else:
        raise ValueError("Unsupported award source: %s" % source)

    currency_raw = row.get("currency_name")
    currency = normalize_currency(currency_raw)
    final_amount = Decimal(final_total) if final_total is not None else None
    return {
        "source_family": "UZEX/Xarid" if source in ("deals", "direct", "civil") else source,
        "procedure_type": procedure_type,
        "procedure_id": procedure_id,
        "lot_id": procedure_id,
        "award_id": award_id,
        "contract_id": row.get("contract_num") or award_id,
        "winner_inn": normalize_inn(row.get("provider_inn")),
        "buyer_inn": normalize_inn(row.get("customer_inn")),
        "winner_name": row.get("provider_name"),
        "buyer_name": row.get("customer_name"),
        "title": row.get("category_name") or row.get("civil_name"),
        "status_raw": raw_status or None,
        "status": status,
        "is_win": status in ("accepted", "contract_published"),
        "is_open_competition": is_open_competition,
        "final_total": final_total,
        "currency": currency,
        "currency_raw": currency_raw,
        "start_total": start_total,
        "above_threshold": above_threshold(final_amount, currency),
        "date_basis": date_basis,
        "awarded_at": awarded_at,
        "published_at": row.get("date_ini"),
        "deadline": row.get("civil_date_end"),
        "evidence_url": evidence_url,
    }


def award_in_window(award: Dict[str, Any], date_from: str, date_to: str) -> Optional[bool]:
    """Сверяет результат по declared ``awarded_at``; не угадывает битые даты."""
    raw_date = str(award.get("awarded_at") or "")[:10]
    try:
        value = date.fromisoformat(raw_date)
    except ValueError:
        return None
    return date.fromisoformat(date_from) <= value <= date.fromisoformat(date_to)


def historical_coverage(award: Dict[str, Any], crawler_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Классифицирует только доказательства из snapshot, не изобретая sent_at.

    Строка фида результатов появляется после дедлайна и потому не подтверждает
    своевременный алерт. ``telegram_message_id`` без времени отправки также не
    позволяет доказать timely capture.
    """
    procedure_id = str(award.get("procedure_id") or "")
    suffix = "/lot/%s" % procedure_id
    matched = [row for row in crawler_rows if str(row.get("source_url") or "").endswith(suffix)]
    if not matched:
        return {
            "outcome": "unknown",
            "timely": None,
            "delivery": "unknown",
            "reason": "no_snapshot_match",
            "rows": [],
        }
    active = [row for row in matched if row.get("deadline")]
    if not active:
        return {
            "outcome": "late_result_only",
            "timely": False,
            "delivery": "unknown",
            "reason": "only_result_rows",
            "rows": matched,
        }
    if any(row.get("telegram_sent_at") for row in active):
        return {
            "outcome": "timely_push",
            "timely": True,
            "delivery": "confirmed",
            "reason": "active_row_with_sent_timestamp",
            "rows": active,
        }
    # notifier.save_alert_seq writes telegram_message_id only after Telegram's
    # sendMessage/sendPhoto endpoint returns HTTP 200.  This establishes delivery
    # acceptance, but its timestamp is not persisted, so it must not be promoted
    # to a claim that the alert was timely.
    if any(row.get("telegram_message_id") is not None for row in active):
        return {
            "outcome": "unknown",
            "timely": None,
            "delivery": "confirmed",
            "reason": "message_time_missing",
            "rows": active,
        }
    if any(row.get("alert_seq") is not None for row in active):
        return {
            "outcome": "unknown",
            "timely": None,
            "delivery": "unknown",
            "reason": "message_time_missing",
            "rows": active,
        }
    return {
        "outcome": "unknown",
        "timely": None,
        "delivery": "unknown",
        "reason": "active_row_without_alert_evidence",
        "rows": active,
    }
