"""Журнал закупок: одна строка на договор/итог любой ленты, ключ — ИНН заказчика.

Зачем. Топ заказчиков нашей продукции и «купили наше мимо нас» нельзя считать
по алертам: у половины нет заказчика, из э-магазинов в заказчики попадает
«КИТАЙ», один лот лежит в трёх лентах (черновик 07.10.2026). Считаем по
договорам — у сделок etender, прямых закупок UZEX, итогов ВМК-69 и карточек
ebirja есть ИНН заказчика.

Модуль чистый: без HTTP, БД и настроек. Строки списков и данные карточек —
разные наборы колонок (list_payload / details_payload): повторный upsert списка
не затирает то, что принесла карточка, и не трогает колонки разбора.
"""
import hashlib
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional

from crawler.core.competitor_audit import normalize_award, normalize_inn

UZEX_FEEDS = ('deals', 'direct', 'civil')
EBIRJA_FEEDS = {
    'shop': 'ebirja_shop',
    'auction': 'ebirja_auction',
    'tender': 'ebirja_tender',
    'selection': 'ebirja_selection',
}
EBIRJA_CARD_URL = 'https://ebirja.uz/ru/contracts/%s/%s'
# Код валюты ebirja → буквенный; "000" карточка показывает как сумы.
_EBIRJA_CURRENCY = {'000': 'UZS', '840': 'USD', '978': 'EUR', '643': 'RUB'}
# Написания валют в лентах UZEX (07.10): сделки — «Сум/Доллар/Евро/Юань»,
# итоги ВМК-69 — «Сом», прямые закупки — коды.
_UZEX_CURRENCY = {'сум': 'UZS', 'сом': 'UZS', 'uzs': 'UZS', 'доллар': 'USD', 'usd': 'USD', 'евро': 'EUR',
                  'eur': 'EUR', 'юань': 'CNY', 'cny': 'CNY', 'rub': 'RUB', 'рубль': 'RUB'}
# Статусы, при которых закупка состоялась или идёт к договору. Отказ и расторжение
# в траты заказчика не входят.
PURCHASE_STATUSES = ('accepted', 'protocol', 'contract_published', 'contract')
_CODE_RE = re.compile(r'^\s*(\d{2}(?:\.\d{1,3}){1,4})')

# Разделы классификатора прямых закупок, где может оказаться полиграфия или
# мерч: только у них читаем детали (позиции договора). Остальное — ~97% потока
# (выборка 07.10: 2,4% строк в «печатных» разделах) — предметом не бывает.
_DIRECT_DETAIL_DIVISIONS = re.compile(
    r'издат|печат|бумаг|одежд|текстил|реклам|изделия готовые прочие|'
    r'минеральные неметаллические|резин|пластмасс|кож', re.I)

# Колонки, которые пишет только чтение карточки/деталей — списки их не передают.
_DETAIL_COLUMNS = ('buyer_inn', 'winner_inn', 'subject', 'subject_codes', 'subject_hash')

# Поля, общие для всех строк списков: PostgREST берёт набор колонок пачки из
# первого объекта, поэтому у всех строк пачки ключи одинаковые.
_LIST_COLUMNS = (
    'feed', 'business_id', 'procedure_id', 'contract_number', 'status',
    'buyer_inn', 'buyer_name', 'buyer_type', 'winner_inn', 'winner_name',
    'category', 'subject', 'subject_codes', 'subject_hash',
    'amount', 'currency', 'amount_uzs', 'start_amount', 'awarded_on',
    'lot_key', 'source_url', 'raw_sha256',
)


def norm_text(value):
    # type: (Any) -> str
    # NUL Postgres в text не хранит (22P05): сборщик чистит ответы API на входе,
    # здесь — вторая линия для любого другого вызова.
    return '' if value is None else ' '.join(str(value).replace('\x00', '').split())


def subject_hash(subject):
    # type: (Any) -> Optional[str]
    text = norm_text(subject).casefold()
    if not text:
        return None
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]


def iso_day(value):
    # type: (Any) -> Optional[str]
    """Дата из любого формата лент: 2026-10-06T22:05:09, 06.10.2026, 10/06/2026."""
    text = str(value or '').strip()[:10]
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _decimal(value):
    # type: (Any) -> Optional[str]
    if value is None or value == '' or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    text = format(number, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _uzex_currency(raw):
    # type: (Dict[str, Any]) -> Optional[str]
    name = norm_text(raw.get('currency_name')).casefold()
    return _UZEX_CURRENCY.get(name)


def purchase_status(feed, raw):
    # type: (str, Dict[str, Any]) -> str
    """Статус закупки по-русски и по-узбекски (сделки etender пишут на узбекском:
    «Баённома шакллантирилган» — протокол, «… рад етилган» — отказ)."""
    if feed == 'direct':
        return 'contract_published'
    text = norm_text(raw.get('deal_status_name') or raw.get('status_name')).casefold()
    if 'бекор' in text or 'расторг' in text:
        return 'cancelled'
    if 'рад ' in text or text.startswith('рад') or 'отклон' in text:
        return 'rejected'
    if 'қабул' in text or 'принят' in text or 'амалга ошган' in text or 'совершен' in text:
        return 'accepted'
    if 'баённома' in text or 'протокол' in text:
        return 'protocol'
    return 'unknown'


def from_uzex(feed, raw, raw_sha256=None):
    # type: (str, Dict[str, Any], Optional[str]) -> Optional[Dict[str, Any]]
    """Строка журнала из записи DealsList / GetDirectPurchases / CivilContracts."""
    if feed not in UZEX_FEEDS:
        raise ValueError('not a UZEX feed: %s' % feed)
    award = normalize_award(feed, raw)
    if feed == 'deals':
        business_id = raw.get('deal_id')
        lot_key = award.get('procedure_id')            # etender.uzex.uz/lot/<trade_id>
    elif feed == 'direct':
        business_id = raw.get('id')
        lot_key = None
    else:
        business_id = raw.get('civil_contract_id') or raw.get('display_id')
        display = norm_text(raw.get('display_id'))
        lot_key = ('result-%s' % display) if display else None   # tenders.external_id «UZEX Результаты»
    business_id = norm_text(business_id)
    if not business_id:
        return None
    currency = _uzex_currency(raw)
    amount = _decimal(award.get('final_total'))
    title = norm_text(award.get('title'))
    row = {
        'feed': feed,
        'business_id': business_id,
        'procedure_id': norm_text(award.get('procedure_id')) or None,
        'contract_number': norm_text(award.get('contract_id')) or None,
        'status': purchase_status(feed, raw),
        'buyer_inn': award.get('buyer_inn'),
        'buyer_name': norm_text(award.get('buyer_name')) or None,
        'buyer_type': norm_text(raw.get('customer_type_name') or raw.get('typ_direct_purchase_name')) or None,
        'winner_inn': award.get('winner_inn'),
        'winner_name': norm_text(award.get('winner_name')) or None,
        # У прямой закупки в списке только раздел классификатора («Вещества
        # химические…») — это категория, а не предмет; предмет дают детали.
        'category': (title or None) if feed == 'direct' else None,
        'subject': None if feed == 'direct' else (title or None),
        'subject_codes': None,
        'subject_hash': None if feed == 'direct' else subject_hash(title),
        'amount': amount,
        'currency': currency,
        'amount_uzs': amount if currency == 'UZS' else None,
        'start_amount': _decimal(award.get('start_total')),
        'awarded_on': iso_day(award.get('awarded_at')),
        'lot_key': lot_key,
        'source_url': award.get('evidence_url'),
        'raw_sha256': raw_sha256,
    }
    return row


def from_ebirja_list(source_key, item, raw_sha256=None):
    # type: (str, Dict[str, Any], Optional[str]) -> Optional[Dict[str, Any]]
    """Строка журнала из записи списка ebirja (collect_ebirja_contract_api.normalize).

    В списке нет ни ИНН, ни предмета — их приносит карточка (apply_ebirja_card).
    """
    feed = EBIRJA_FEEDS[source_key]
    business_id = norm_text(item.get('procedure_id'))
    if not business_id:
        return None
    currency = _EBIRJA_CURRENCY.get(str((item.get('raw') or {}).get('currency') or ''))
    amount = _decimal(item.get('amount'))
    lot = norm_text(item.get('lot_number'))
    return {
        'feed': feed,
        'business_id': business_id,
        'procedure_id': lot or None,
        'contract_number': norm_text(item.get('contract_number')) or None,
        'status': 'contract',
        'buyer_inn': None,
        'buyer_name': norm_text(item.get('buyer_name')) or None,
        'buyer_type': None,
        'winner_inn': None,
        'winner_name': norm_text(item.get('winner_name')) or None,
        'category': None,
        'subject': None,
        'subject_codes': None,
        'subject_hash': None,
        'amount': amount,
        'currency': currency,
        'amount_uzs': amount if currency == 'UZS' else None,
        'start_amount': None,
        'awarded_on': iso_day(item.get('awarded_at')),
        'lot_key': lot or None,
        'source_url': EBIRJA_CARD_URL % (source_key, business_id),
        'raw_sha256': raw_sha256,
    }


def list_payload(row):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """Колонки строки списка для upsert: без тех, что приносит карточка.

    У прямых закупок и ebirja предмет и (у ebirja) ИНН приходят только из
    деталей; передай их список как null — и следующий прогон затрёт карточку.
    Сделки и итоги ВМК-69 несут предмет и ИНН в самом списке.
    """
    drop = ()  # type: tuple
    if row['feed'] == 'direct':
        drop = ('subject', 'subject_codes', 'subject_hash')
    elif row['feed'].startswith('ebirja_'):
        drop = _DETAIL_COLUMNS
    return {k: row.get(k) for k in _LIST_COLUMNS if k not in drop}


def _code(text):
    # type: (Any) -> Optional[str]
    match = _CODE_RE.match(str(text or ''))
    return match.group(1) if match else None


def _localized(value):
    # type: (Any) -> str
    if isinstance(value, dict):
        return norm_text(value.get('ru') or value.get('uz') or value.get('cyrl'))
    return norm_text(value)


def _dedup(items):
    # type: (Iterable[str]) -> List[str]
    out = []  # type: List[str]
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def ebirja_card_details(source_key, card):
    # type: (str, Dict[str, Any]) -> Dict[str, Any]
    """Колонки, которые даёт публичная карточка договора ebirja.

    Э-магазин: позиция заказа (`order.product_log`) — название с кодом
    («24.51.20.110-00001-Cho‘yan quvur») и классификатор. Аукцион/тендер/отбор:
    классификаторы лота.
    """
    customer = card.get('customer') or {}
    producer = card.get('producer') or {}
    titles = []  # type: List[str]
    codes = []  # type: List[str]
    if source_key == 'shop':
        product = (card.get('order') or {}).get('product_log') or {}
        classifier = product.get('classifier') or {}
        titles = [norm_text(product.get('title')),
                  _localized(classifier.get('title_ru') or classifier.get('title_uz'))]
        codes = [norm_text(classifier.get('code')) or _code(product.get('title'))]
    else:
        proc = card.get('auction') if source_key == 'auction' else card.get('tender')
        proc = proc if isinstance(proc, dict) else {}
        for line in proc.get('auction_classifiers') or proc.get('tender_classifiers') or []:
            classifier = (line or {}).get('classifier') or {}
            titles.append(_localized(classifier.get('title_ru') or classifier.get('title_uz')))
            # Описание позиции заказчиком («Pos terminallari (HUMO) xarid…»)
            # часто точнее классификатора — судье профиля оно нужно.
            titles.append(norm_text((line or {}).get('description')))
            codes.append(norm_text(classifier.get('code')))
        titles.append(norm_text(proc.get('title')))
    subject = '; '.join(_dedup(titles))[:1000] or None
    return {
        'buyer_inn': normalize_inn(customer.get('tin')),
        'winner_inn': normalize_inn(producer.get('tin')),
        'subject': subject,
        'subject_codes': _dedup(codes) or None,
        'subject_hash': subject_hash(subject),
    }


def direct_details(detail):
    # type: (Dict[str, Any]) -> Dict[str, Any]
    """Предмет прямой закупки — названия позиций договора (`js_details`)."""
    names = []  # type: List[str]
    for item in detail.get('js_details') or []:
        if isinstance(item, dict):
            names.append(norm_text(item.get('product_name') or item.get('description')))
    subject = '; '.join(_dedup(names))[:1000] or None
    return {'subject': subject, 'subject_codes': None, 'subject_hash': subject_hash(subject)}


def details_payload(row, details, fetched_at):
    # type: (Dict[str, Any], Dict[str, Any], str) -> Dict[str, Any]
    """Upsert только карточных колонок по ключу (feed, business_id)."""
    payload = {'feed': row['feed'], 'business_id': row['business_id'], 'details_fetched_at': fetched_at}
    # Набор ключей зависит только от ленты (у прямых закупок ИНН в деталях нет —
    # он пришёл списком и не трогается): пачка upsert однородна.
    for key in _DETAIL_COLUMNS:
        if key in details:
            payload[key] = details[key]
    return payload


def needs_details(row):
    # type: (Dict[str, Any]) -> bool
    """Нужна ли строке карточка: ebirja — всегда, прямые — в «печатных» разделах."""
    if row.get('details_fetched_at'):
        return False
    feed = row.get('feed') or ''
    if feed.startswith('ebirja_'):
        return True
    return feed == 'direct' and bool(_DIRECT_DETAIL_DIVISIONS.search(row.get('category') or ''))
