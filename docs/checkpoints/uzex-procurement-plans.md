
# Сессия верификации 2026-06-11 — GetProcurementAnnouncements + GetMinimizedLotsList

## Проверка 1: GetProcurementAnnouncements — anonymous POST, from/to пагинация
curl -X POST "https://xarid-api-purchase.uzex.uz/Common/GetProcurementAnnouncements" -H "User-Agent: Chrome/125" -H "Content-Type: application/json" -H "Origin: https://xarid.uzex.uz" -d '{"from":1,"to":5}'
Результат: 200 OK, JSON-массив. total_count=275 (в каждой записи). Поля записи:
id, delivery_days, delivery_days_date, date_ini, sts_id, mfi_id/mfi_name ("Mfi one"/"Mfi two" — мусорные заглушки), date_submission, proc_plan_id, proc_plan_name (ПРЕДМЕТ ЕСТЬ — текст плана: "План закупок АО Шаргун кумир 2026 год", "Сырьё", "ЮРИДИК АДАБИЁТЛАР САВДО ДЎКОНИНИ ЖОРИЙ ТАЪМИРЛАШ"), address_country/region/district/street, address_contact_fullname/phone, person_fullname/position/address/email/phone, rn.
Вывод: ПОДТВЕРЖДЕНО. Анонимно отдаёт. proc_plan_name = свободный текст (есть предмет, но качество переменное: иногда название плана целиком, иногда конкретный предмет). Объём небольшой: 275 записей всего, даты date_ini от 2024 до 2025-12. НЕТ цены/суммы в list. НЕТ ОКЭД-категории.

## Проверка 2: GetProcurementAnnouncements — полная выгрузка 275 записей, свежесть
curl -X POST .../Common/GetProcurementAnnouncements -d '{"from":1,"to":275}'
Результат: все 275 записей получены одним запросом (cap не упёрся). Распределение date_ini по годам:
2021:1, 2022:253, 2023:14, 2024:6, 2025:1 (последняя 2025-12-22, АО Шаргун кумир, план 2026).
Print-like по ключевым словам (бумаг/полиграф/журнал/адабиёт...): 8 записей, ВСЕ кроме двух — 2022 год ("Бумага А4" x3, "Покупка бумага", "Бумага для офисной техники"), 2024: "ЮРИДИК АДАБИЁТЛАР..." (ремонт магазина, не печать), 2022: "Подписка на мед.журналы".
Вывод: канал ЖИВОЙ технически, но ДАННЫЕ МЕРТВЫ: ~92% записей — 2022 год, новых ~1-7 в год. Это НЕ опережающий поток спроса — фича объявлений планов закупок на старом xarid практически заброшена заказчиками (план-графики, видимо, переехали на exarid/dxarid /plan разделы). Скорость: ~0.02 записи/день.

## Проверка 3: GetMinimizedLotsList — поиск endpoint в SPA-бандле
- /Common/GetMinimizedLotsList на xarid-api-trade и xarid-api-purchase = HTTP 404.
- Скачан бандл xarid.uzex.uz/main-es2015.36915cacb6e57685637d.js (10.3 МБ). Найдено: метод getMinimizedLotsList → POST {auctionUrl}/Common/GetMinimizedLotsList.
- Карта API-хостов из environment бандла: auctionUrl=https://xarid-api-auction.uzex.uz, auctionx=https://xarid-api-auctionx.uzex.uz, common=xarid-api-common, prequest=xarid-api-prequest, purchase=xarid-api-purchase, shop=xarid-api-shop, trade=xarid-api-trade.
- ПРОБЛЕМА: xarid-api-auction.uzex.uz (89.236.218.120) и xarid-api-auctionx.uzex.uz (89.236.218.118) — TCP connection timeout 30-60s с Mac (не 403, именно молчаливый дроп — похоже на geo-blocking или фильтр). Остальные хосты (purchase) отвечают нормально.

## Проверка 4: GetMinimizedLotsList — хост, auth, валидация
- Endpoint: POST https://xarid-api-auction.uzex.uz/Common/GetMinimizedLotsList (auctionUrl из бандла). НЕ trade/purchase.
- БЕЗ заголовка validation → HTTP 500 {"status":500,"message":"Приложение : Not Valid"}. Т.е. аукционный хост ТРЕБУЕТ validation-заголовок (в отличие от GetProcurementAnnouncements на purchase, который сейчас отдаёт 200 БЕЗ него).
- Хост 89.236.218.120 жив (TCP open, connect 0.18s); ранний timeout был сетевым транзиентом.
- validation = base64(RSA-PKCS1v15( url + '~' + dd.MM.yyyy , publicKey )). publicKey (node-forge, 1024-bit) хардкожен в main-бандле:
  -----BEGIN PUBLIC KEY-----
  MIGeMA0GCSqGSIb3DQEBAQUAA4GMADCBiAKBgH8lx9sqVlIPIPvXSzzMOM1a0QjQ
  7oFbQKNntR4ckpa5pczfsLDDb0fzVz0FvImpgncTZLSJHAlaU4S/6EVmgPSgMm8n
  6pjKBGKQKlKQ6AHgVK3aaZ95fvsXezIETlIfP2YITMhbtlwV2uUvqlwGc2xrBrsd
  uscHPwmkfEiflDJ/AgMBAAE=
  -----END PUBLIC KEY-----
  Токен день-стабильный, переиспользуемый между эндпоинтами (подтверждено прошлой сессией: один токен сработал на shop+competitions).
- ДАННЫЕ (подтверждено прошлой сессией, checkpoints-extracted.json:4322, HTTP 200): 592 активных аукциона (total_count=592). Поля list-записи: id, customer_type ("Korporativ"/др), district_name, category_name (ОКЭД-текст, НЕ предмет!), start_cost, min_cost, display_no (напр "26121007403993"), total_count. Пример: id=403993 "Машины и оборудование...", start_cost 11.4М, min_cost 9.576М, Карши.
- Body фильтра: {"region_ids":[], "from":N, "to":M} (+ опц. customer_type_id, category_id, search). Подтверждено из бандла (modalFilter поля: region_ids, customer_type_id, category_id, search).
- DETAIL карточка: GET https://xarid-api-auction.uzex.uz/Common/GetLot/{id} → содержит js_details[] (product_name/quantity = ПРЕДМЕТ лота). Это единственное место с предметом печати; в list только ОКЭД.
- Deep-link UI: https://xarid.uzex.uz/auction/detail/{id} (роут path:"auction" + auction/detail/ подтверждён в бандле).

## Проверка 5: Соседние plan-эндпоинты (богаче Announcements) — из бандла + live
В бандле найдены ВСЕ procurement-эндпоинты на purchaseUrl: GetProcurementAnnouncements, GetProcurementPlans, GetProcurementNotices, GetProcPlanDetails, GetProcurementNoticeYears/Months/Seasons/Sources, GetProcurementPlanProdTypes, GetProcurementAnnouncementMfiIpfos.

### GetProcurementPlans (САМЫЙ БОГАТЫЙ план-канал) — anonymous, БЕЗ validation, 200
POST https://xarid-api-purchase.uzex.uz/Common/GetProcurementPlans -d '{"from":1,"to":N}'
total_count=1631. Поля: id, procurement_name (РЕАЛЬНЫЙ ПРЕДМЕТ в list! "Бумага", "Кабели силовые...", "Водоэмулция"), date_ini, sts_id, year_id/year_name, season_id/season_name (квартал/годовой), prod_type_id/prod_type_name (Товар/Услуга/Работа), has_announcement, notice_project_name, rn, total_count.
СВЕЖЕСТЬ: 2021:24, 2022:1326, 2023:205, 2024:58, 2025:9, 2026:9. Print-like (бумаг/полиграф/журнал/каталог/блокнот...): 119 записей, но абсолютное большинство — 2022-2023. Свежих 2026 всего 9 записей суммарно.
DETAIL: GET https://xarid-api-purchase.uzex.uz/Common/GetProcPlanDetails/{id} (НЕ POST — POST даёт 404; GET/{id} = 200). Возвращает js_months[] (запланированные месяцы поставки) + procurement_name + season. БЕЗ количества/цены/контактов.

### GetProcurementNotices (план-графики, "notice project") — anonymous, 200
POST .../Common/GetProcurementNotices -d '{"from":1,"to":N}'  → total_count=487. Поля: id, project_name ("План-график закупок ... на 2026 год", "SOLOD"), date_ini (свежие до 2026-04-20), fin_source_id/name, year, season. project_name = название план-графика (не предмет позиции).

## ИТОГ — оба канала

### 1) GetProcurementAnnouncements (xarid планы/объявления "опережающий спрос")
- ХОСТ/МЕТОД: POST https://xarid-api-purchase.uzex.uz/Common/GetProcurementAnnouncements
- AUTH: АНОНИМНО, validation-заголовок НЕ требуется (отдаёт 200 с UA+Origin+Referer). Подтверждено дважды.
- BODY: {"from":1,"to":N} (1-based; cap не упирался на 275; та же from/to пагинация что у competitions).
- ОБЪЁМ: total_count=275. МЁРТВО: 253/275 = 2022 год, новых ~1-7/год, последняя 2025-12. Это НЕ живой опережающий поток.
- ПОЛЯ list (есть КОНТАКТЫ заказчика — уникально!): proc_plan_name (предмет/название плана), mfi_name (заглушки "Mfi one/two"), date_submission, delivery_days, address_region/district/street, address_contact_fullname/phone, person_fullname/position/email/phone. БЕЗ цены, БЕЗ ОКЭД.
- PRINT: технически предмет есть в list (proc_plan_name) → keyword работает без detail. НО данные мёртвые → канал малоценен как поток лидов. Уникальная ценность — контакты заказчика (email/phone) для тех немногих записей.
- DEEP-LINK: https://xarid.uzex.uz/purchase/procurement-common/list (только list-роут; индивид. detail-роут в SPA не выделен — карточка через тот же list/фильтр).
- ЛУЧШЕ: GetProcurementPlans (1631, procurement_name=предмет в list) и GetProcurementNotices (487) — те же anonymous/purchase, богаче, но тоже скошены к 2022. Вся plan-подсистема СТАРОГО xarid.uzex.uz — легаси (актуальные план-графики, вероятно, на exarid/dxarid /plan или etender).

### 2) GetMinimizedLotsList (электронные обратные аукционы)
- ХОСТ/МЕТОД: POST https://xarid-api-auction.uzex.uz/Common/GetMinimizedLotsList (auctionUrl, НЕ trade/purchase).
- AUTH: ТРЕБУЕТ validation-заголовок. Без него → HTTP 500 {"message":"Приложение : Not Valid"}. validation = base64(RSA-PKCS1v15(url+'~'+dd.MM.yyyy, pubKey)); pubKey 1024-bit в бандле (см. Проверку 4); токен день-стабильный, переиспользуемый. С токеном → 200 (подтверждено прошлой сессией: 592 аукциона).
- BODY: {"region_ids":[],"from":N,"to":M} (+ опц. customer_type_id, category_id, search).
- ОБЪЁМ: total_count=592 активных аукциона (ЖИВОЙ канал, в отличие от планов).
- ПОЛЯ list: id, customer_type, district_name, category_name (ТОЛЬКО ОКЭД-текст, БЕЗ предмета), start_cost, min_cost, display_no. БЕЗ предмета/позиций в list.
- DETAIL: GET https://xarid-api-auction.uzex.uz/Common/GetLot/{id} → js_details[] (product_name/quantity = ПРЕДМЕТ). Единственное место с предметом печати.
- PRINT: ВЫСОКАЯ как канал, но keyword по list мимо (только ОКЭД) → ОБЯЗАТЕЛЕН detail-enrichment (GetLot/{id}, складывать js_details в search_text). Та же проблема recall, что у xarid-competitions.
- DEEP-LINK: https://xarid.uzex.uz/auction/detail/{id}.

### ПОДВОДНЫЕ КАМНИ
- xarid-api-* хосты требуют ПОЛНЫЙ Chrome User-Agent (bare "Mozilla/5.0" → 500 "Missing User-Agent" в прошлых тестах) + Origin/Referer https://xarid.uzex.uz.
- validation-заголовок enforced ВЫБОРОЧНО по хосту: auction = да; purchase/GetProcurement* = сейчас НЕТ. Не полагаться — может включиться; генератор токена готов (pubKey в бандле, алгоритм url+'~'+дата).
- mfi_name в announcements = заглушки "Mfi one/Mfi two" (не реальный заказчик); реальный заказчик — в person_/address_ полях.
- auction-хост дал transient TCP timeout (30-60s) перед тем как ответить — добавить retry в краулер.
- Бандл xarid: main-es2015.36915cacb6e57685637d.js (хэш может смениться при редеплое — pubKey/хосты перечитывать из свежего index.html).
