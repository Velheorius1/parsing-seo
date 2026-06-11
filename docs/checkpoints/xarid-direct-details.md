# Xarid GetDirectPurchases — обогащение detail/positions (2026-06-11)

Канал: xarid-direct, list endpoint `https://xarid-api-purchase.uzex.uz/Common/GetDirectPurchases`, POST {"from":N,"to":M}, total_count=228868.

## 1. Структура LIST-ответа (GetDirectPurchases)
curl -s -X POST "https://xarid-api-purchase.uzex.uz/Common/GetDirectPurchases" -H "Content-Type: application/json" -d '{"from":0,"to":2}'
Замечание: from=0,to=2 вернул 3 записи (диапазон inclusive [from..to]).

КЛЮЧЕВОЙ ВЫВОД: list УЖЕ содержит победителя и цену. Поля одной записи:
- id: 4419099 (внутренний id для detail-роутов)
- display_id: "261200214419099" (публичный id)
- category_name: ОКЭД-категория (напр. "Услуги в области архитектуры...", "Оборудование компьютерное, электронное и оптическое")
- provider_name: ПОБЕДИТЕЛЬ/поставщик (напр. 'Unicon.uz fan-texnika...', 'УЧРЕЖДЕНИЕ "DAVLAT KADASTRLARI..."')
- provider_inn: ИНН поставщика (напр. 200898586)
- contract_sum: ЦЕНА контракта (напр. 795400.0, 19607000.0, 2611668.0)
- currency_name: "UZS"
- contract_num: номер договора "E-26-10254"
- contract_date: "2026-06-10T00:00:00"
- typ_direct_purchase_name: тип ("Прямые закупки" / "Единый поставщик")
- has_details_extra: 0/1 — флаг наличия доп.деталей (позиций)
- has_discussion_protocol: 1 — флаг протокола обсуждения
- status_name: "Опубликован"
- customer_name + customer_inn: ЗАКАЗЧИК (напр. "MIKROKREDITBANK ATB", 200547792)
- customer_type: "юр. лицо резидент"
- decree_name: основание (ЗРУ-684...)
- total_count: 228868

ВЫВОД: для конкурентной разведки (кто купил у кого почём) detail НЕ нужен — всё в list.
Detail (has_details_extra) нужен только для line-item ПОЗИЦИЙ (что именно: наименование товара, кол-во, цена за ед).

## 2. DETAIL endpoint найден (РЕВЕРС РОУТА)
Паттерн detail по всем UZEX-purchase каналам = PATH-параметр (НЕ query string):
  Common/GetCompetition/{id}, Common/GetOffer/{id}, Common/GetLot/{id}.
По аналогии прямые закупки:
  GET https://xarid-api-purchase.uzex.uz/Common/GetDirectPurchase/{id}   → HTTP 200 JSON
(query-string варианты ?id= и POST {id} → ВСЕ 404. Работает только /{id} в пути.)

curl -s "https://xarid-api-purchase.uzex.uz/Common/GetDirectPurchase/4419099" -H "User-Agent: <Chrome UA>"

Detail-payload (id 4419099) добавляет к list ещё:
- description_text: краткое описание ("Топосиёмка ишларини бажариш")
- delivery_days: 10 (срок поставки)
- customer_address + customer_ls_account: адрес + банк. счёт заказчика
- benefit_name / founder_name / branch_name / user_id
- status_id: 5
- js_details[] ← ПОЗИЦИИ (line items)! поля: order_num, product_name, description,
  quantity, unit_name, price, cost, currency_name, month_name, avans (аванс),
  js_properties[] (вложенные характеристики: "Единица измерения"="усл. ед",
  "Наличие поэтапной оплаты"="Нет" и т.п.)
- js_contract_files[] ← ДОГОВОР PDF: {file_id, file_name (uuid.pdf), file_path
  ("files/user-files/2026/6/10"), file_ext, custom_name "Direct_Purchase_Contract_<id>.pdf", typ_id:3}
- js_protocol_files[] / js_reason_files[] / js_default_files[] — прочие вложения
- has_details_extra (флаг доп.деталей), has_discussion_protocol

ВЫВОД: detail-обогащение работает, отдаёт ПОЗИЦИИ (что именно куплено, кол-во, цена/ед)
+ ссылку на скан договора PDF. Используем id (внутренний, НЕ display_id) из list.

---
## ДОПОЛНЕНИЕ 2026-06-11 (верификация фильтрации + печатные контракты)

### 3. SERVER-SIDE ФИЛЬТР — подтверждено: работает ТОЛЬКО category_id
Тест: к телу `{"from":0,"to":3}` добавляли по одному параметру, сравнивали total_count.
- name / search / category_name / oked / date_begin+date_end / contract_num / nested {"filters":{...}} → ВСЕ ИГНОРИРУЮТСЯ (total_count неизменен 228785, та же выдача).
- `category_id` → РАСПОЗНАЁТСЯ. С невалидным id (1,5,18,50,100) → items=0. С валидным → фильтрует.
- Альтернативные list-эндпоинты (GetDirectPurchasesByFilter / SearchDirectPurchases / *Filter) → 404.
ВЫВОД: единственный server-side фильтр публичного списка = `category_id` (целое, exact-match по ОКЭД-категории).

### 4. СПРАВОЧНИК КАТЕГОРИЙ (реверс из main-es2015 bundle)
SPA xarid.uzex.uz: `libGetCategories(){ http.get("/Lib/GetCategories") }` — namespace **/Lib/**, не /Common/ (потому ранее 404).
Хост справочника = **xarid-api-trade.uzex.uz** (НЕ purchase!):
  GET https://xarid-api-trade.uzex.uz/Lib/GetCategories  → 200, JSON-массив 88 шт, поля {id, name}.
Печатные категории (ОКЭД):
  - id=113765  "Услуги печатные и услуги по копированию звуко- и видеозаписей, а также программных средств"
  - id=113434  "Бумага и изделия из бумаги"

### 5. ВАЛИДАЦИЯ ФИЛЬТРА (decisive test)
curl -s -X POST "https://xarid-api-purchase.uzex.uz/Common/GetDirectPurchases" -H "Content-Type: application/json" -H "User-Agent: <Chrome>" -d '{"from":0,"to":5,"category_id":113765}'
  → items=6, total_count=898, ВСЕ записи category_name="Услуги печатные..." (чистая выдача).
  → category_id=113434 → total_count=1015, все "Бумага и изделия из бумаги".
ИТОГ: вместо листания 228 786 контрактов — таргет печати напрямую: 898 (печатные услуги) + 1015 (бумага) ≈ 1 913 контрактов через 2 значения category_id. Server-side фильтр печати РАБОТАЕТ.

### 6. ЖИВЫЕ ПЕЧАТНЫЕ КОНТРАКТЫ (победитель + цена, найдены 2026-06-11)
| id | категория | заказчик | ПОБЕДИТЕЛЬ (ИНН) | цена |
|----|-----------|----------|------------------|------|
| 4418217 | Услуги печатные | O`ZBEKISTON TEMIR YO`LLARI AJ | Давлат Белгиси МЧЖ (306612737) | 33 152 000 UZS |
| 4417877 | Услуги печатные | UZBEKISTAN AIRWAYS TECHNICS | "TOSHKENT-HUMO XALQARO AEROPORTI" (310825178) | 20 000 000 UZS |
| 4418840 | Услуги печатные | O'ZNEFTEGAZ BURG'ULASH ISHLARI | "KARSHI LIDER" MChJ (304179644) | 19 870 000 UZS |
| 4418632 | Услуги печатные | БУХОРО ВИЛОЯТ ИИБ ЖИЭК | "NAQSH UNIVERSAL SERVIS" (300375808) | 4 140 000 UZS |
| 4417998 | Бумага и изделия | ООО SIRDARYO SUV TA'MINOTI | "ALPOMISH MEDIA KONSERT" (310915604) | 8 000 000 UZS |
| 4418460 | Бумага и изделия | "ELEKTRON ONLAYN-AUKSIONLAR" AJ | ГУП Типография (200796334) | 3 360 000 UZS |
| 4418635 | Бумага и изделия | БУХОРО ВИЛОЯТ ИИБ ЖИЭК | "KANS PLYUS BUXARA" XK (309287696) | 2 998 800 UZS |

### 7. ПОЗИЦИИ (detail) — подтверждено на печатном контракте
GET /Common/GetDirectPurchase/4417877 → js_details[0] = {order_num:1, product_name:"Полиграфические услуги", quantity:1.0, price:20000000.0, cost:20000000.0}.
Detail НЕ содержит category_id (только category_name) — для маппинга категории брать из list или /Lib/GetCategories.

### 8. РЕЦЕПТ ОБОГАЩЕНИЯ (полиграфия, итог)
Шаг 0 (1 раз): GET https://xarid-api-trade.uzex.uz/Lib/GetCategories → запомнить id 113765 + 113434.
Шаг 1 (list, server-side фильтр): POST https://xarid-api-purchase.uzex.uz/Common/GetDirectPurchases
        body {"from":N,"to":N+99,"category_id":113765} (и отдельно 113434). Пагинация from/to inclusive, шаг ~100. total_count в каждой записи. offset cap не достигается (≈900/1015 < 3000).
        → УЖЕ содержит победителя (provider_name/inn), цену (contract_sum/currency), заказчика (customer_*), № и дату договора, has_details_extra.
Шаг 2 (detail, только если has_details_extra=1 ИЛИ нужны позиции/скан): GET /Common/GetDirectPurchase/{id} (внутренний id, не display_id)
        → js_details[] (наименование/кол-во/цена-ед), js_contract_files[] (PDF договора).
Deep-link карточки: https://new-xarid.uzex.uz/home/shop/detail/{id}?elektron=true
Заголовок: Chrome User-Agent желателен (хост может резать дефолтные UA), Content-Type: application/json.

---
## ИТОГ
1. DETAIL прямой закупки: `GET https://xarid-api-purchase.uzex.uz/Common/GetDirectPurchase/{внутр.id}` → позиции (js_details) + скан договора PDF (js_contract_files). Path-param, не query. Подтверждено на печатном контракте.
2. SERVER-SIDE ФИЛЬТР: ДА, но только `category_id` (целое). category_name/search/name/date/oked/contract_num/nested filters → игнорируются. Справочник категорий: `GET https://xarid-api-trade.uzex.uz/Lib/GetCategories` (88 шт, {id,name}). Печать = id 113765 (печатные услуги, 898 контрактов) + 113434 (бумага, 1015). Таргет печати = ~1 913 записей вместо 228 786.
3. 3-5 живых печатных контрактов найдены (см. секцию 6): крупнейшие — Давлат Белгиси МЧЖ 33.15M UZS (УзЖД), TOSHKENT-HUMO 20M (UZ Airways Technics), KARSHI LIDER 19.87M (O'zneftegaz).
СТАТУС: все 3 задачи (detail-роут / server-side фильтр / печатные контракты) — ПОДТВЕРЖДЕНЫ вживую 2026-06-11.
