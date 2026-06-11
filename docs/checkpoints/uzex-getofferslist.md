
## РЕВЕРС JS-БАНДЛА (2026-06-11)
xarid.uzex.uz = Angular SPA. Бандл: main-es2015.36915cacb6e57685637d.js (10.3 MB)
Сервис: `getOffersList(e){return this.http.post(this.getFullUrl("/Common/GetOffersList"),e)}`
`getFullUrl(e){return l.a.shopUrl+e}` где shopUrl="https://xarid-api-shop.uzex.uz"

ВЫВОД: endpoint = **POST https://xarid-api-shop.uzex.uz/Common/GetOffersList** (НЕ JSON-RPC, обычный REST POST)
Env-config (production):
  appUrl=https://xarid.uzex.uz
  shopUrl=https://xarid-api-shop.uzex.uz
  authUrl=https://id.uzex.uz, authAPIUrl=https://idapi.uzex.uz
  commonUrl=https://xarid-api-common.uzex.uz
Deep-link карточка: https://xarid.uzex.uz/shop/lot-details/{id}

## REQUEST BODY SCHEMA (из chunk 22-es2015 = ShopCommonModule)
Caller `loadProducts()`:
  this.filter.from=(this.times-1)*this.limit+1   // 1-based!
  this.filter.to=this.times*this.limit
  service.getOffersList(this.filter)
Дефолты: limit=12, times=1, offset=0 → первая страница from=1, to=12
Response: list с полем `total_count` (на каждом элементе), total = l[0].total_count
Filter model (`new J.i`) поля:
  from (int, 1-based), to (int)
  product_name (string), product_code (string)
  category_id, category_name
  is_local_manufacturer (0/1/typeId), producer_country_id, producer_country_name
  region_name, district_id, district_name
Сортировка: priceSort (LowToHigh/HighToLow) — отдельный механизм

## ✅ LIVE ПОДТВЕРЖДЕНО (2026-06-11) — анонимно, без токена
curl -X POST https://xarid-api-shop.uzex.uz/Common/GetOffersList \
  -H "Content-Type: application/json" -H "Origin: https://xarid.uzex.uz" \
  -d '{"from":1,"to":3}'
→ HTTP 200, total_count=552807 (выше заявленных 551,548 — растёт)
Поля элемента: total_count, rn, id, display_no (SO27085270), product_code (28.99.39.190-00018),
  product_name (Пожарный щит), date_ini (MM/DD/YYYY HH:MM:SS), price (4000000.0), amount, producer_country_name,
  file_ext, file_name, file_path (files/user-files/YYYY/M/D), currency_name (Сум), unit_name (шт), category_id
PRINT-релевантно есть: product_code префиксы видны (17/18 = бумага/печать по ОКЕД-подобному классификатору).

## ФИЛЬТРЫ — что работает (live тесты 2026-06-11)
- product_name (free text) → ИГНОРИРУЕТСЯ сервером (вернул newest, не отфильтровал "визитк")
- product_code ПРЕФИКС "17" → 0 результатов (НЕ префиксный матч)
- product_code ТОЧНЫЙ "28.99.39.190-00018" → total 321, все "Пожарный щит" ✅ РАБОТАЕТ (exact SKU)
- is_local_manufacturer:1 → 115600 (только УЗБ производитель) ✅ РАБОТАЕТ
- ВАЖНО: при to:5 вернулось 12 элементов → 'to' возможно игнорится/min-page, проверяю cap
ВЫВОД: серверная фильтрация = по ТОЧНОМУ product_code (каталожный SKU) + is_local_manufacturer + (category_id/region — проверить).
Свободный текст product_name НЕ фильтрует. Для print: нужны точные коды из классификатора (17/18/22.29 пластик).

## ПАГИНАЦИЯ — ТОЧНАЯ СЕМАНТИКА (live 2026-06-11, повторная сессия)
Тесты window size:
  from=1 to=12 -> 12 элементов (page1, ids 27085631..27085601)
  from=13 to=24 -> 12 элементов (page2, ids 27085600.., consecutive — НЕ overlap)
  from=1 to=13/20/24/30/40/50/100 -> ВСЕГДА ровно 12 элементов
ВЫВОД: **page size ЖЁСТКО = 12, неизменяем**. Сервер игнорирует ширину окна (to-from), всегда отдаёт 12 строк начиная с `from`.
- `from` = 1-based offset (старт строки). `to` практически игнорится для счёта (cap from+11).
- Пагинация: from=1, потом from=13, from=25, ... шаг +12. (или from += 12)
- total_count живёт на КАЖДОМ элементе (l[0]['total_count']).
- rn в ответе = 0 (не заполнен, не использовать для offset).
ПОСЛЕДСТВИЕ ДЛЯ КРОЛА: 552,892 / 12 ≈ 46,075 запросов на полный обход. Дорого. Лучше фильтровать (product_code/category_id/is_local_manufacturer) до обхода.

## OFFSET CAP + SEARCH (live 2026-06-11)
- DEEP OFFSET: from=500000 -> 12 элементов OK (first id 26127252); from=552000 -> 12 OK (first 25817638).
  => **НЕТ cap 3000** на этом REST-эндпоинте (cap 3000 был у JSON-RPC платформы, тут не применяется). Полный обход 552k реален.
- product_name "Бумага" -> total_count 552894, names=[Портландцемент, Персональный компьютер, ...] => **product_name СЕРВЕРОМ ИГНОРИТСЯ** (подтверждено повторно, фильтра нет).
- category_id:1 -> ПУСТОЙ ответ (невалидный id или меняет поведение). Нужны валидные category_id из каталога.

## КАТАЛОГ / SEARCH ENDPOINTS (реверс bundle 2026-06-11)
Поиск товара = НЕ free-text по офферам. Сначала резолв category_id -> product_code через каталог (база tradeUrl):
- GET https://xarid-api-trade.uzex.uz/Lib/GetCategories  -> дерево категорий (classifier)
- GET https://xarid-api-trade.uzex.uz/Lib/GetProducts/{categoryId}?keyword={text} -> товары категории (SKU + product_code)
- GET https://xarid-api-trade.uzex.uz/Lib/GetProductsWithProps?categoryId={id}&productName={name} -> товары+свойства
- GET https://xarid-api-trade.uzex.uz/Lib/GetExclusiveProductCodes, /Lib/GetRegions, /Lib/GetCountries, /Lib/GetRegionsDistrict
Все БАЗЫ хостов (env production):
  shopUrl=xarid-api-shop.uzex.uz (GetOffersList, GetProductPrices)
  tradeUrl=xarid-api-trade.uzex.uz (/Lib/* каталог, поиск товаров)
  commonUrl=xarid-api-common.uzex.uz, purchaseUrl=xarid-api-purchase.uzex.uz
  auctionUrl=xarid-api-auction.uzex.uz, etenderUrl=apietender.uzex.uz
Детальный прайс оффера: POST /Common/GetProductPrices (shopUrl).

## КАТЕГОРИИ — PRINT-РЕЛЕВАНТНЫЕ (live /Lib/GetCategories, 88 top-level, анонимно HTTP200)
- 113434 "Бумага и изделия из бумаги"
- 113765 "Услуги печатные и услуги по копированию звуко- и видеозаписей..."
- 125883 "Услуги издательские"
- 126986 "Услуги рекламные и услуги по исследованию конъюнктуры рынка"

## ✅ КЛЮЧЕВОЕ: category_id ФИЛЬТР РАБОТАЕТ (live 2026-06-11) — ВХОД ДЛЯ PRINT
GetOffersList с category_id даёт print-офферы напрямую (product_name в list!):
- category_id:113434 (Бумага) -> total_count 14,640. Коды 17.* (Бумага офсетная, Картон, туалетная).
- category_id:113765 (Услуги печатные) -> total_count 1,974. Коды 18.12.* / 18.13.* = ЧИСТЫЙ PRINT:
    18.12.12.000-00002 Баннер
    18.12.12.000-00010 Услуга по широкоформатному печатанию баннеров
    18.12.14.000-00007 Услуга по изготовлению сборников
    18.12.12.000-00006 Услуга по изготовлению настольного каталога
    18.13.30.000-00003 Услуга по фотопечати
    18.12.19.190-00005 Услуга по установке баннера
(ранний category_id:1 -> EMPTY был просто невалидный id, фильтр исправен)
ВЫВОД: для print-краулинга — обходить по category_id 113765 (1,974), 113434 (14,640), 125883, 126986. НЕ полный обход 552k.

## ГОЛОВНЫЕ ТРЕБОВАНИЯ / LIVENESS / DEEP-LINK (live 2026-06-11)
- **User-Agent ОБЯЗАТЕЛЕН (browser-like).** Без UA или с curl/8.0 -> HTTP 500 {"status":500,"message":"Приложение : Missing User-Agent header"}. Mozilla/5.0 проходит. Полный Chrome UA надёжнее.
- Origin header НЕ обязателен (без него tc=552913 OK). Content-Type: application/json обязателен.
- Авторизация/токен НЕ нужны (всё анонимно).
- LIVENESS: print-офферы cat 113765 newest = СЕГОДНЯ 06/11/2026 10:16 (Баннер) — канал живой, офферы постятся в реальном времени. date_ini формат MM/DD/YYYY HH:MM:SS. SO-префикс = Sale Offer (активная продажа).
- DEEP-LINK https://xarid.uzex.uz/shop/lot-details/27085490 -> HTTP 200 (Angular SPA route, id из поля `id`).
- Детальная карточка/история цен: POST https://xarid-api-shop.uzex.uz/Common/GetProductPrices (filter с product_code, возвращает items[].total_count). Для print-мониторинга НЕ нужна — list самодостаточен (product_name, price, code, unit прямо в GetOffersList).

## ИТОГ
КАНАЛ ПОДТВЕРЖДЁН (holds=true). Endpoint: POST https://xarid-api-shop.uzex.uz/Common/GetOffersList
- Анонимно, нужен только browser User-Agent + Content-Type: application/json.
- Объём: 552,913 офферов всего (растёт ~+1k/час по newest). Print-срез по категориям:
    113765 Услуги печатные = 1,974 | 113434 Бумага = 14,640 | 125883 Издательские = 3,986 | 126986 Рекламные = 368
- product_name прямо в list. Пагинация: page size ЖЁСТКО 12, from=1-based offset, шаг from+=12. Deep offset OK (нет cap 3000).
- ФИЛЬТРЫ: category_id ✅, product_code (exact SKU) ✅, is_local_manufacturer ✅. product_name/free-text ✗ (игнорится — резолвить через /Lib/GetProductsWithProps).
- Каталог категорий: GET https://xarid-api-trade.uzex.uz/Lib/GetCategories (88 шт, анонимно).
- Deep-link: https://xarid.uzex.uz/shop/lot-details/{id}
РЕКОМЕНДАЦИЯ адаптеру: обходить по 4 print-категориям (≈20,968 офферов сумм), а не весь 552k. ~1,748 запросов/полный print-обход при page=12.

## ПОПРАВКА UA (live 2026-06-11) — ВАЖНО для адаптера
UA-проверка СТРОГАЯ, не просто "содержит Mozilla":
- "Mozilla/5.0" -> HTTP 500 Missing User-Agent
- "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" -> HTTP 500 Missing User-Agent
- ПОЛНЫЙ Chrome UA "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36" -> 200 OK
ВЫВОД: нужен ПОЛНЫЙ браузерный UA с суффиксом Chrome/...Safari/... (вероятно проверка на наличие "Chrome"). Использовать реальную строку Chrome целиком.
