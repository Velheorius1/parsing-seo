
# ===== РЕВЕРС etender.uzex.uz — сессия 2026-06-11 (NotDealedList / DealsList) =====

## main.js реверс — API host + endpoints
Bundle: https://etender.uzex.uz/main.c7fa7448c1739171b519.js (9.3 MB)
environment: serverUrl="https://apietender.uzex.uz", serverUrlNew="https://api-etender-new.uzex.uz"
Найденные endpoints:
- POST /api/common/NotDealedList  (service.getFails(filter)) — несостоявшиеся торги
- POST /api/common/DealsList      (service.getDeals(filter))  — заключённые сделки
- POST /api/CivilContracts/GetResulted / GetNotResulted (другой канал — "civil contracts", body {from,to,date_From,date_To,keyword,customer_Name,customer_Inn,provider_Inn})
- /api/provider/GetTradesDealed, GetTradesNotDealed (требуют auth — provider scope)
Body = tradeListFilter (класс M): поля From, To, TypeId, System_Id=0, currencyId. + keyword/category_id/region_id (видны как строки в бандле).

## NotDealedList — ПОДТВЕРЖДЁН анонимно (HTTP 200)
curl -s -A "<Chrome UA>" -X POST 'https://apietender.uzex.uz/api/common/NotDealedList' \
  -H 'Content-Type: application/json' -H 'Origin: https://etender.uzex.uz' -H 'Referer: https://etender.uzex.uz/' \
  -d '{"From":1,"To":3,"System_Id":0}'
ОТВЕТ (массив объектов, первый):
{"trade_id":495387,"display_no":"26120012495387","start_date":"2026-06-04T09:35:55","end_date":"2026-06-11T09:35:55",
 "category_name":"Сурхондарьё вилояти Узун тумани “Окмачит” (Бетон)","start_cost":23399320.38,
 "participants_count":0,"customer_name":"FAVQULODDA VAZIYATLAR V","customer_inn":"305501353",
 "is_local_manufacturs":true,"status_id":12,"status_name":"Торг не состоялся","rn":1,"total_count":53509}
ВЫВОД: ПОДТВЕРЖДЕНО. Анонимно. total_count=53509 записей (весь архив несостоявшихся).
Поля: trade_id, display_no, start_date, end_date, category_name (предмет+тип в скобках), start_cost,
participants_count, customer_name, customer_inn, is_local_manufacturs, status_id(12=не состоялся), status_name, rn, total_count.

## DealsList — ПОДТВЕРЖДЁН АНОНИМНО (ранее считался "закрыт/unknown") — HTTP 200
curl -s -A "<Chrome UA>" -X POST 'https://apietender.uzex.uz/api/common/DealsList' \
  -H 'Content-Type: application/json' -H 'Origin: https://etender.uzex.uz' -H 'Referer: https://etender.uzex.uz/' \
  -d '{"From":1,"To":3,"System_Id":0}'
ОТВЕТ (первый объект):
{"deal_date":"2026-06-11T09:59:02","deal_id":170371,"trade_id":489015,"display_no":"26110012489015",
 "start_cost":454068720.0,"deal_cost":280342384.56,"currency_name":"Сум","participants_count":2,
 "customer_name":"Учкургон туман тиббиёт бирлашмаси","customer_type_name":"Budget buyurtmachi","customer_inn":"200103332",
 "provider_name":"SAIDIKROM OTA XK","provider_inn":"203694181","deal_status_name":"Баённома шакллантирилган",
 "proposal_status_name":"Протокол сформирован","category_name":"ШТТЁ Аутсорсинг","is_local_manufacturs":false,
 "rn":1,"total_count":156809}
ВЫВОД: DealsList ОТКРЫТ анонимно! total_count=156809 сделок. ЦЕНА ПОБЕДИТЕЛЯ = deal_cost, победитель = provider_name/provider_inn.
ВАЖНО про body: рабочий body = МИНИМАЛЬНЫЙ {"From":N,"To":M,"System_Id":0}.
  Body с "TypeId":0 → возвращает ПУСТОЙ массив [] (size 2)! TypeId:0 фильтрует всё. НЕ слать TypeId если не нужен фильтр по типу.
Поля DealsList: deal_date, deal_id, trade_id, display_no, start_cost(начальная), deal_cost(ПОБЕДА), currency_name,
participants_count, customer_name, customer_type_name, customer_inn, provider_name, provider_inn,
deal_status_name, proposal_status_name, deal_contract_date, deal_contract_status_name, deal_contract_kazna_status_name/id,
category_name(предмет), is_local_manufacturs, beneficiary, founder, can_comment,
contract_file_name/path/ext/sizes/date, additional_protocol_file_*, rn, total_count.

## DealsList — Keyword фильтр РАБОТАЕТ (ищет по category_name=предмет) — print-релевантные сделки с ценами победителей
Body: {"From":1,"To":N,"System_Id":0,"Keyword":"<слово>"}
- Keyword="блокнот": total_count=6. #136781 "Печатная продукция(календарь и блокнот)" start=65 650 000 → deal=45 024 000 | победитель ООО PRINTUZ
- Keyword="полиграф": total_count=59. #168230 Uzbekistan Airways bosma mahsulot / полиграфия start=516 537 200 → deal=298 088 000 | DEKOS GROUP X/K
- Keyword="буклет": total_count=8. #120987 "Услуга по печатанию... Художественных буклетов к выставки" start=70 000 000 → deal=69 000 000 | YTT NAVRO‘ZBOYEVA
- Keyword="бумаг": total_count=54 — НО много false-positive ("ценные бумаги", банковский учёт). Нужен фильтр-стоп-слова.
- Keyword="коробк": total_count=11 — в основном НЕ печать (распределит. коробки, пресс-формы). Слабый print-сигнал.
ВЫВОД: Keyword фильтрует category_name. Сильные print-keywords: полиграф(59), блокнот(6), буклет(8), печат*. Слабые/шумные: бумаг, коробк.

## DealsList — ещё print-keywords (total_count по category_name)
- "печат" -> 220 (САМЫЙ сильный сигнал). #168890 CREDO PRINT GROUP deal=355M
- "полиграф" -> 59 | "блокнот" -> 6 | "буклет" -> 8 | "этикет" -> 3 (#154294 LASER COMPUTERS) | "наклейк" -> 2 (#136783 ООО Print Media)
- false/слабые: "конверт" -> 10 (проектирование), "каталог" -> 10 (IT-каталог), "бумаг" -> 54 (ценные бумаги), "коробк" -> 11
- 0 результатов: "визитк", "листовк"
РЕКОМЕНДУЕМЫЕ print-keywords для DealsList/NotDealedList: печат, полиграф, блокнот, буклет, этикет, наклейк, типограф, бланк, календар.

# ===== ВЕРИФИКАЦИЯ сессия 2026-06-11 (повторная проверка прошлых находок) =====

## NotDealedList + DealsList — ПОВТОРНО ПОДТВЕРЖДЕНЫ live (HTTP 200, анонимно)
NotDealedList total_count: 53509 -> 53510 (растёт, живые данные).
DealsList total_count: 156809 -> 156810 (растёт). Оба открыты без авторизации.

## ИСПРАВЛЕНИЕ прошлой находки: TypeId НЕ фильтрует (прошлая заметка про "TypeId:0 → пусто" НЕ воспроизводится)
curl ... -d '{"From":1,"To":3,"System_Id":0,"TypeId":0}'  -> count=2, total=156810 (НЕ пусто!)
curl ... -d '{"From":1,"To":3,"System_Id":0,"TypeId":1}'  -> count=2, total=156810 (то же самое)
ВЫВОД: TypeId:0 и TypeId:1 дают ОДИНАКОВЫЙ полный набор (156810). TypeId в этом endpoint игнорируется/не влияет.
Прошлая заметка "TypeId:0 возвращает пустой массив" — артефакт момента, СЕЙЧАС не воспроизводится. Можно безопасно НЕ слать TypeId.

## Пагинация From/To: To ЭКСКЛЮЗИВНА, count = To - From. БЕЗ cap 3000 (это REST, не /rpc JSON-RPC)
From:1,To:2 -> 1 запись (rn=1). From:1,To:3 -> 2 записи (rn 1,2). From156000,To156003 -> 3 записи.
From:3001,To:3002 -> HTTP 200, отдаёт запись (deal от 2026-04-02). Глубокая пагинация работает.
From:156000 (у самого конца архива 156810) -> HTTP 200, deal_date 2022-02-15 (самые старые сделки).
ВЫВОД: весь архив 156810 сделок пагинируется анонимно, cap-а 3000 НЕТ на этом endpoint.
  (cap 3000 из контекста платформы относится к JSON-RPC /rpc на xtx, НЕ к etender REST /api/common/*.)
Рекоменд. шаг страницы: To-From = 100..500 записей за запрос.

## FRESH print-релевантные DealsList (Keyword=полиграф, total=59, цены победителей) — 2026-06-11
- #26120012477602 | "Uzbekistan Airways" bosma mahsulot | start=516 537 200 -> deal=298 088 000 | "DEKOS GROUP" X/K
- #25120012465642 | Полиграфические услуги | start=120 000 000 -> deal=74 900 000 | ООО TERRA PRINT KLASTER
- #25120012451222 | Закупка услуг по изготовлению/оснащению | start=650 000 000 -> deal=550 000 000 | ООО HOUSTON
- #25120012452413 | Bosma mahsulotlarni ishlab chiqarish | start=829 289 776 -> deal=547 680 000 | ЧП PECHATNIK VOSTOKA
- #25120012450680 | Рекламно-сувенирная, полиграфическая прод | start=2 012 816 600 -> deal=1 274 084 672 | ООО PRINTUZ

## NotDealedList — Keyword ТОЖЕ РАБОТАЕТ (фильтр по category_name). Keyword=печат -> total_count=94
Print-несостоявшиеся (start_cost + participants_count = конкурентная разведка):
- #25120012453309 | Keng formatli nashr materiallar | start=322 694 400 | participants=2 | UNIVERSAL MOBILE SYSTEMS
- #25120012452325 | дизайн+печать | start=97 666 667 | participants=4 | ASIA TRANS GAS
- #25120012451033 | Fleksografik chop / типографские чернила | start=231 191 671 | participants=2 | Shurtan Gaz Kimyo
- #25120012447931 | Poligrafiya/tipografiya (kitob chop) | start=19 600 000 | participants=2 | Shurtan Gaz Kimyo
ВЫВОД: NotDealedList поддерживает тот же Keyword-фильтр. 94 несостоявшихся print-лота — лиды (заказчик хотел печать, торг не состоялся = можно зайти напрямую).

## ПОЛНАЯ КАРТОЧКА — GET /api/common/GetTrade/{trade_id}/0 (анонимно, HTTP 200)
Реверс из main.js: getLot(id) = http.get("/api/common/GetTrade/"+id+"/0"). НЕ POST, GET с path-param.
getParticipants(id) = GET /api/common/GetProposalUsers/{trade_id} (для #489015 вернул [] — участники скрыты/пусто).
GetTrade/489015/0 -> полная карточка:
{"id":489015,"display_no":...,"start_date","end_date","clarific_date","start_cost":454068720,
 "valuation_id":2,"valuation_name":"Балл усули","budget_products":"<JSON-строка массива позиций>"...}
budget_products = строка-JSON с ДЕТАЛЬНЫМИ позициями лота:
  Id, Order_Num, Category_Id/Name, Product_Id/Name, Product_Type_Id, Quantity, Price, Cost, Description,
  Product_Code (классификатор ЕНКТ напр "56.29.19.000-00004"), Delivery_Term, Js_Properties (ед.изм, свойства).
ВЫВОД: GetTrade даёт построчную спецификацию закупки (товар+кол-во+цена+код) — нужно для точного print-скоринга.
Доступно анонимно. Эндпоинт детали найден реверсом, НЕ угадан (guessed TradeInfo/GetTradeInfo/... все 404).

## Все /api/common/* endpoints из бандла (реверс):
NotDealedList, DealsList, TradeList, MasterTradeList, SolarTradeList (активные торги),
GetTrade (деталь), GetProposalUsers (участники), GetMessages/AddMessage/GetJournalActions (Q&A),
GetMapStats/GetTopStats (статистика), DownloadFile (файлы).
Прочие scope (требуют auth): api/Customer/*, api/Provider/*, api/Member/* (GetTradeBids, GetRankedTrades), api/Trade/Create.

## ФИЛЬТР Date_From/Date_To — НЕ работает на DealsList/NotDealedList (игнорируется)
curl ... -d '{"From":1,"To":3,"System_Id":0,"Date_From":"2026-06-01T00:00:00","Date_To":"2026-06-11T23:59:59"}'
  -> total_count=156810 (НЕ изменился — фильтр проигнорирован).
Keyword=печат + Date_From 2026-01-01 -> total=220 (= тот же 220 что без даты). Дата НЕ режет.
Date_From/Date_To в бандле принадлежат фиче "Мониторинг сделок" (отдельный flow, не common/DealsList).
ВЫВОД: на DealsList/NotDealedList работают ТОЛЬКО 2 фильтра — From/To (пагинация) и Keyword (поиск по category_name).
Фильтрацию по дате делать КЛИЕНТСКИ: результаты отсортированы deal_date/start_date DESC (новые первыми);
пагинировать From/To пока deal_date >= cutoff, затем стоп. Инкрементальный краул: тянуть страницы пока не дошли до уже виденного deal_id/max(deal_date) с прошлого прогона.

## Сводка рабочего body (итоговая для адаптера)
DealsList:     POST /api/common/DealsList     body {"From":1,"To":200,"System_Id":0[,"Keyword":"печат"]}
NotDealedList: POST /api/common/NotDealedList  body {"From":1,"To":200,"System_Id":0[,"Keyword":"печат"]}
Деталь:        GET  /api/common/GetTrade/{trade_id}/0   (построчные позиции budget_products)
Заголовки ОБЯЗАТЕЛЬНЫ: Chrome User-Agent, Origin: https://etender.uzex.uz, Referer: https://etender.uzex.uz/, Content-Type: application/json (для POST).
НЕ слать TypeId (игнорируется, но в прошлом давал глюк пустого ответа — лучше опустить).

## ЗАГОЛОВКИ — НЕ обязательны (исправление!) + размер страницы 200 OK
curl БЕЗ кастомных заголовков (default curl UA, без Origin/Referer) -> HTTP 200, данные отдаются.
curl с Chrome UA без Origin/Referer -> тоже 200.
ВЫВОД: apietender.uzex.uz/api/common/* НЕ гейтит по UA/Origin/Referer. Нужен только Content-Type: application/json для POST.
  (Chrome UA в рецепте — для перестраховки/будущего, но фактически не требуется.)
Размер страницы: From:1,To:201 -> 200 строк (rn 1..200). Можно тянуть по 200-500 за запрос. ~157k сделок / 200 = ~785 запросов на полный архив.

## ===== ИТОГ =====
Канал etender.uzex.uz — ОБА списка ОТКРЫТЫ АНОНИМНО (опровергнут прежний статус DealsList="возможно закрыт"):

1) NotDealedList (несостоявшиеся лоты, конкурентная разведка):
   POST https://apietender.uzex.uz/api/common/NotDealedList  body {"From":1,"To":200,"System_Id":0}
   total_count ~53 510 (растёт). Поля: trade_id, display_no, start_date, end_date, category_name(предмет),
   start_cost, participants_count, customer_name, customer_inn, is_local_manufacturs, status_id(12), status_name, rn, total_count.
   Print-фильтр: Keyword="печат" -> 94 несостоявшихся print-лота (лиды: заказчик хотел печать, торг сорвался).

2) DealsList (заключённые сделки + ЦЕНЫ ПОБЕДИТЕЛЕЙ — ранее "нужен реверс/возможно закрыт", по факту ОТКРЫТ):
   POST https://apietender.uzex.uz/api/common/DealsList  body {"From":1,"To":200,"System_Id":0}
   total_count ~156 810 (растёт). КЛЮЧ: deal_cost = цена победителя, provider_name/provider_inn = победитель,
   start_cost = начальная цена, participants_count, customer_*, contract_file_* (путь к договору).
   Print-фильтр: Keyword="печат" -> 220 сделок, "полиграф" -> 59.

3) Деталь (построчная спецификация): GET /api/common/GetTrade/{trade_id}/0 -> budget_products (товар, кол-во, цена, код ЕНКТ).

Фильтры списков: ТОЛЬКО From/To (пагинация, To эксклюзивна) и Keyword (поиск по category_name).
Date_From/Date_To ИГНОРИРУЮТСЯ — фильтр по дате клиентский (сортировка DESC по дате, пагинировать до cutoff).
TypeId игнорируется (опускать). Cap 3000 НЕ применяется (весь архив пагинируется). Заголовки/UA не требуются.
print-keywords (надёжные): печат, полиграф, блокнот(6), буклет(8), этикет(3), наклейк(2), типограф, бланк, календар.
Шумные/исключать: бумаг(ценные бумаги), коробк, конверт, каталог(IT). Пустые: визитк, листовк.
