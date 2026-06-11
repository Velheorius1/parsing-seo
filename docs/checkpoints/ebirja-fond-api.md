
# ===== РЕ-ВЕРИФИКАЦИЯ 2026-06-11 (verifier session) =====

## auction-product/all (встречные аукционы)
curl 'https://api.ebirja.uz/fond-api/external/auction-product/all'
-> {"status":401,"state":1087,"message":"Full authentication is required to access this resource"}
HTTP 401, time 0.95s
ВЫВОД: эндпоинт ЖИВ но теперь ЗАКРЫТ авторизацией (раньше был открытый JSON). state:1087.

## seller-product/all-auction (биржевые аукционы поставщиков, было 42 шт)
curl 'https://api.ebirja.uz/fond-api/external/seller-product/all-auction'
-> {"status":401,"state":1087,"message":"Full authentication is required to access this resource"}
HTTP 401
ВЫВОД: тоже ЗАКРЫТ авторизацией.

## Без префикса /fond-api/
curl 'https://api.ebirja.uz/external/auction-product/all' -> HTTP 404 (Tomcat)
ВЫВОД: путь существует только под /fond-api/. Бэкенд = Tomcat/Java.

## КРИТИЧНО: проверка уже-краулящихся анонимных эндпоинтов
curl 'https://api.ebirja.uz/fond-api/external/contract/all?page=0&size=2'      -> 401 (был анонимный, [CRAWLED] как ebirja-ext-contracts)
curl 'https://api.ebirja.uz/fond-api/external/seller-product/all?page=0&size=2' -> 401 (был анонимный, [CRAWLED] как ebirja-ext-products)
curl 'https://api.ebirja.uz/fond-api/external/seller-product/price-dynamic-for-menu' -> 401 (был анонимный)
Также не помогли: браузерный User-Agent, Origin: https://ebirja.uz, Referer, Accept: application/json.
ВЫВОД: ВЕСЬ /fond-api/external/* теперь закрыт авторизацией (state:1087), а не только 3 новых эндпоинта.
СЛЕДСТВИЕ: существующий ebirja-краулер (ext-contracts, ext-products) СЛОМАН — отдаёт 401 на всё.

## Куда мигрировали данные / фронт
- Старые страницы ebirja.uz/ru/trade/{announcements,reverse-auction} живы (HTTP 200), но это Next.js-оболочки БЕЗ инлайн-лотов (нет mainPrice/totalCount/цен в SSR). Все ссылки ведут на нового оператора xarid.ebirja.uz (Toshkent tovar-xomashyo birjasi).
- Нет Bearer/token/Authorization в SSR HTML — публичного токена для fond-api не выдаётся.
- xarid-api.ebirja.uz ЖИВ (Yii/PHP), отвечает структурными 404 на угаданные роуты (/, /statistics, /api/v1/statistics) — НЕ 401. Это ОТДЕЛЬНЫЙ публичный-ish REST нового оператора, но точные роуты не найдены (нужна отдельная discovery-сессия). Live-данные ebirja, вероятно, теперь там.

## ИТОГ
ВЕРДИКТ ПО 3 ЦЕЛЕВЫМ ЭНДПОИНТАМ fond-api: claim "анонимный чистый JSON" БОЛЬШЕ НЕ ДЕРЖИТСЯ.
- external/auction-product/all      -> 401 (был анонимный JSON, vol=0)
- external/seller-product/all-auction -> 401 (был анонимный, 42 шт)
- external/seller-product/one?number= -> 401 (был анонимный, деталь)
Закрыт ВЕСЬ /fond-api/external/* (state:1087, "Full authentication is required"), включая уже-краулящиеся contract/all и seller-product/all.

СЛЕДСТВИЕ #1 (важнее задачи): существующий ebirja-краулер (ebirja-ext-contracts, ebirja-ext-products) теперь отдаёт 401 на ВСЁ — СЛОМАН. Action item для команды parsing-seo.
СЛЕДСТВИЕ #2: краулить fond-api анонимно НЕЛЬЗЯ. Нужна авторизация (E-IMZO через app.ebirja.uz — Winch не зарегистрирован) ИЛИ пивот на xarid-api.ebirja.uz (Yii REST, нужна route-discovery).

РЕКОМЕНДАЦИЯ: НЕ краулить fond-api. Print-релевантность и так была СРЕДНЯЯ-НИЗКАЯ (биржа = сырьё/ГСМ/стройматериалы, полиграфия редка). Приоритет low: 1) починить/отключить мёртвый ebirja-краулер; 2) при желании — отдельная discovery xarid-api.ebirja.uz; мониторить раз в день не стоит — анонимный доступ убран намеренно.
