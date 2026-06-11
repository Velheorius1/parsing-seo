
# XT-Xarid ref_selection_public — верификация 2026-06-11

## 1. Базовый read работает
curl -X POST 'https://api.xt-xarid.uz/rpc' -H 'Content-Type: application/json' -d '{"id":1,"jsonrpc":"2.0","method":"ref","params":{"ref":"ref_selection_public","op":"read","limit":2,"offset":0}}'
Результат: HTTP 200, {"result":[{"type":"selection","totalcost":2.502e8,"status":"docs_objections","remain_time":-3239,"name":"Отбор","meta":{"lots":[...],"good_maps":[{"unit":"шт","price":9700000,"name":"Шины и покрышки..."}],"fin_src":[...],"company_name":"Камчикавтойул..."}}...
Вывод: ПОДТВЕРЖДЕНО — канал жив, анонимный доступ, good_maps с товарами/ценами внутри meta.

## 2. Полная структура записи
curl ... '{"method":"ref","params":{"ref":"ref_selection_public","op":"read","limit":1,"offset":0}}'
TOP KEYS: area, close_at, close_docs_objections_at, company_id, company_name, contract_id, contract_number, currency, docs_objections_remain_time, good, good_count, green, id, is_new_multilot, lang, lot_count, meta, multilot, name, part_count, publicated_at, remain_time, status, totalcost, type
META KEYS: area_path, company_inn, company_name, fin_src, good_maps, lots
Пример: id=7653909, type=selection, status=docs_objections, remain_time=-3257, docs_objections_remain_time=115330, good_count=4, currency=UZS, company_id=62, area='33.1182'
Вывод: ПОДТВЕРЖДЕНО — id числовой (id-space общий с proc_id?), good_maps в meta, статус и тайминги есть.

## 3. Фильтр по статусу работает (read), op=count НЕ существует
- op:"count" → внутренняя ошибка сервера (и с filters тоже). Подсчёт только пробингом offset.
- read + filters {"status":"open"} → OK: 7640231 open remain=536547 400M ГУП INTERFORUM; 7610207 open 666M HUDUDIY ELEKTR TARMOQLARI; 7597925 open 3.26B QARSHI YO'LLARDAN...
Вывод: ПОДТВЕРЖДЕНО — server-side фильтр по status работает в op=read.

## 4. Объём активных (status=open) — пробинг offset
offset=100→1 строка, 110→1, 125→1, 150→0, 200→0, 500→0
Вывод: открытых отборов сейчас ~126-150 (между 125 и 150).

## 5. Open-выборка целиком + print-фильтр
- limit капится на 100 ("Значение должно быть <= 100"). Забрал 2 страницами: open всего = 133 записи.
- Client-side фильтр good_maps по [блокнот/бумаг/печат/бланк/этикетк/картон/...]: 4 хита, из них реально print-релевантный 1-2:
  * id=7538833 АО «Национальный банк ВЭД РУ», 123.48 млн UZS — "Услуга по печатанию карточек микропроцессорных", 52500 усл.ед × 2352 UZS — РЕЛЕВАНТНО (печать карточек)
  * id=7575882 (бумаг→хлопчатоБУМАЖные ткани — ложный хит), id=7567608 (картон в стройке — проверить), id=7549592 (печат→платы — ложный)
Вывод: print-доля в отборах низкая (~1-3% живых), но реальные печатные отборы ЕСТЬ. Нужен точный keyword-матчинг (бумаг → ложные срабатывания на "хлопчатобумажные").

## 6. docs_objections = 54 записи
curl ... filters {"status":"docs_objections"} limit=100 → 54 строки, строгие print-ключи (бланк/этикетк/блокнот/типограф...) — 0 хитов в этом статусе сейчас.

## 7. Объём и статусы
- Без фильтра page0 (limit=100): статусы docs_objections=52, docs_objections_summary=25, open=23. id range 7572326-7654204.
- offset=2999 без фильтра → строка есть (id=6173366, status=not_realized) → всего записей >3000 (offset cap), глубже — архив (not_realized и пр.).
- Активные сейчас: open=133, docs_objections=54, docs_objections_summary ~25-50.
Вывод: канал держит полный архив (>3000), активное окно ~200-250 записей.

## 8. Полная карточка через /urpc get_proc — РАБОТАЕТ
curl -X POST 'https://api.xt-xarid.uz/urpc' -d '{"id":1,"jsonrpc":"2.0","method":"get_proc","params":{"proc_id":7538833}}'
→ result: {procedure:"selection", status:"open", fields:{address, advance, anno, close_time, company_details, contract_props, currency, delivery_properties, documentation, exchange_rates, ...60+ ключей}, docs_objections, objections, ...}
Вывод: ПОДТВЕРЖДЕНО — полная карточка отбора (включая документацию и условия) доступна анонимно по proc_id (= id из ref_selection_public).

## 9. Сравнение с hayotbirja — ЭТО ТОТ ЖЕ КАНАЛ
curl -X POST 'https://api.hayotbirja.uz/rpc' -d тот же body (ref_selection_public, limit=100, offset=0)
→ 100 строк, статусы docs_objections=52/docs_objections_summary=25/open=23 — ИДЕНТИЧНО xt-xarid.
→ Пересечение id: 100 из 100. Топ-5 id совпадают 1-в-1: 7654204, 7653909, 7653716, 7653369, 7653344.
→ Сравнение стабильных полей (status/totalcost/name/company/good_count/...) — записи идентичны.
Вывод: ПОДТВЕРЖДЕНО — api.xt-xarid.uz и api.hayotbirja.uz = один бэкенд (white-label площадки одной биржевой платформы), общее id-space. ref_selection_public на xt-xarid ДУБЛИРУЕТ уже краулящийся hayotbirja-selection. Отдельный адаптер НЕ нужен — разве что как fallback-хост.

## 10. Архив (offset 200-400) — print-примеры и полный жизненный цикл статусов
curl ... offset=200/300, limit=100, без фильтра → 200 записей.
Статусы: close=62, not_realized=42, open=25, tech_check_docs=25, objections=24, cancel=7, docs_objections_summary=6, check_affilation_and_debts=4, commercial_checking=3, agree_objections=2 (сортировка по умолчанию НЕ строго по статусу/дате).
Print-находки:
- id=7461957 [docs_objections_summary] Ўзйўлкўприк кластери Бухоро — "Блокнот" 20 дона × 45000 UZS
- id=7409579 [docs_objections_summary] Нишон тумани халк таълими — "Блокнот" 54 dona × 20000 UZS
- id=7434970 [close] Карши шахар касб-хунар мактаби — "Бумага для офисной техники цветная" 10 қадоқ × 65000 UZS
Из активного окна (212 уникальных): 1 сильный print-хит — id=7538833 [open] АО «НБ ВЭД РУ» "Услуга по печатанию карточек микропроцессорных" 52500 × 2352 UZS = 123.48 млн.
Ложные срабатывания client-side фильтра: "гипсокартон", "наждачная бумага", "печатные платы", "хлопчатобумажные" — нужен exclude-список.

## 11. Существующий конфиг hayotbirja-selection (crawler/config/sources.yaml:2324)
- id: hayotbirja-selection, adapter: jsonrpc, url: https://api.hayotbirja.uz/rpc, rpc_ref: ref_selection_public, dedup_group: "birja-selection", deep-link: https://hayotbirja.uz/procedure/{external_id}/core, pagination offset/100/5 pages.
Вывод: xt-xarid ref_selection_public = ПОЛНЫЙ ДУБЛИКАТ этого канала (id и контент 1-в-1, см. секцию 9).

## ИТОГ
1. Канал ref_selection_public на api.xt-xarid.uz/rpc — ЖИВОЙ, анонимный, заявленное ПОДТВЕРЖДЕНО: отборы покупателей с good_maps (товары/кол-во/цены), статусы open/docs_objections/docs_objections_summary + полный lifecycle (tech_check_docs, objections, commercial_checking, close, cancel, not_realized...).
2. Объём: активных open=133, docs_objections=54, docs_objections_summary~25-50 (активное окно ~200-300); всего в ref >3000 (offset cap 2999 ещё отдаёт данные).
3. Ограничения API: limit<=100 (иначе bad_params), op="count" не существует (internal error), server-side filters {"status": "..."} работает в op=read.
4. Полная карточка: POST /urpc {"method":"get_proc","params":{"proc_id":<id>}} — работает анонимно, 60+ полей (документация, условия, адрес).
5. ГЛАВНОЕ: это ТОТ ЖЕ канал что уже краулящийся hayotbirja-selection — api.xt-xarid.uz и api.hayotbirja.uz = один бэкенд, общее id-space, записи идентичны 100/100. Отдельный НОВЫЙ канал НЕ нужен; ценность xt-xarid — только как fallback-хост в dedup_group "birja-selection".
6. Print-релевантность: низкая плотность (~0.5-1% записей), но реальные кейсы есть (блокноты, офисная бумага, печать карточек). Client-side фильтру нужен exclude: гипсокартон/наждачная бумага/печатные платы/хлопчатобумажные.
