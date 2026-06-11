
# ============================================================
# Сессия 2026-06-11: detail-endpoints GetCompetition/{id} (xarid) + GetTrade/{id}/0 (etender)
# Гипотеза: list не несёт предмет закупки → позиции только в detail
# ============================================================
## 1. etender list: POST apietender.uzex.uz/api/common/TradeList — ЖИВОЙ
curl -X POST 'https://apietender.uzex.uz/api/common/TradeList' -H 'User-Agent: Chrome/126' -H 'Content-Type: application/json' -d '{"from":0,"to":5,"lot_number":null,"customer_inn":null,"product_name":null,"category_id":null,"region_id":null,"type":1}'
Результат: 200, массив лотов. total_count=663 активных (type=1). Поля list: rn, id, display_no, name (название тендера, НЕ позиции), start/end_date, cost, seller_name/tin, region/district, currency. category_name=null у всех.
Живые id: 494666, 494309, 494846, 494817.
Вывод: ПОДТВЕРЖДЕНО — list несёт только name тендера (часто общая формулировка), позиций нет. Анонимно, Chrome UA достаточно.
## 2. etender detail: GET apietender.uzex.uz/api/common/GetTrade/{id}/0 — ПОДТВЕРЖДЁН
curl 'https://apietender.uzex.uz/api/common/GetTrade/494666/0' -H 'User-Agent: Chrome/126'
Результат: 200 JSON. КЛЮЧЕВОЕ: поле budget_products = СТРОКА с вложенным JSON (нужен json.loads второго уровня!).
Внутри budget_products[]: Id, Category_Name, Product_Id, Product_Name ("Услуга по подключению...к централизованной системе горячего водоснабжения"), Quantity, Price, Cost, Description, Product_Code ("35.30.12.140-00001" — ЕНКТ/ОКЭД с ДЕФИСОМ, не underscore), Js_Properties[] (ед.изм. и хар-ки), Delivery_Term, Month_Name.
Также в detail: status_name, customer_name/tin, contacts[] (ФИО+должность!), tech_file_path (PDF техзадание), advance_payment_perc, pledge_value, js_fields[] (критерии оценки), rest_time.
Поле products=null (другой тип процедур?), позиции в budget_products.
Вывод: ПОДТВЕРЖДЕНО — предмет закупки (позиции) ТОЛЬКО в detail. GET, анонимно, Chrome UA.
## 3. etender GetTrade — латентность и образцы (3 id)
id=494309 HTTP:200 TIME:1.22s SIZE:28827 | 33.12.15.000-00001 Услуга по текущему ремонту лифтов qty 1 price 34M
id=494846 HTTP:200 TIME:1.30s SIZE:39243 | 71.12.12.130-00004 Услуга по разработке проектно-сметных работ price 400M
id=494817 HTTP:200 TIME:1.32s SIZE:39344 | 71.12.31.000-00004 Работы геологические price 40M
Вывод: латентность detail ~1.2-1.3s, размер 28-40 КБ. 4/4 id из list открылись. budget_products парсится стабильно (json.loads строки).
## 4. etender TradeList фильтр product_name — ИГНОРИРУЕТСЯ сервером
POST TradeList с "product_name":"полиграф" и "product_name":"печат" → оба раза те же лоты, total_count=663 (фильтр не применился).
Вывод: server-side фильтра по предмету НЕТ (либо параметр зовётся иначе) → отбор print-лотов возможен ТОЛЬКО клиентски после detail-fetch (budget_products[].Product_Name/Product_Code). Это подтверждает корень "0-2 алерта": list.name часто общая фраза на узбекском, без позиций.
## 5. xarid list: POST xarid-api-purchase.uzex.uz/Common/GetCompetitions — ЖИВОЙ
curl -X POST 'https://xarid-api-purchase.uzex.uz/Common/GetCompetitions' -H 'Content-Type: application/json' -H 'User-Agent: Chrome/126' -d '{"from":0,"to":5}'
Результат: 200, TIME 0.98s. total_count=4254 активных конкурсов.
Поля list: id, end_date_submitting_offers, customer_region/district_name, category_name (ШИРОКАЯ категория, напр. "Услуги профессиональные..."), cost, currency_name. НЕТ даже name! НЕТ заказчика! НЕТ позиций.
Живые id: 22477, 22476, 22475, 22474, 22471.
Вывод: ПОДТВЕРЖДЕНО — xarid list ещё беднее etender: предмет закупки в list отсутствует полностью, идентификация print-лотов по list НЕВОЗМОЖНА (category_name слишком широкий).
## 6. xarid detail: GET xarid-api-purchase.uzex.uz/Common/GetCompetition/{id} — ПОДТВЕРЖДЁН
curl 'https://xarid-api-purchase.uzex.uz/Common/GetCompetition/22477' -H 'User-Agent: Chrome/126'
Результат: 200, TIME 2.29s, SIZE 3165 B.
КЛЮЧЕВОЕ: js_details[] — НАСТОЯЩИЙ массив (не строка, в отличие от etender budget_products): order_num, product_name ("Услуга по проведению экспертизы системы информационной безопасности..."), description (узб.), quantity, price, cost, js_properties[] (ед.изм., вид расчёта).
Бонус-поля: customer_name/inn/phone, organizer_name (ФИО + ЛИЧНЫЙ телефон + email контактного лица!), competition_description, delivery_address, js_files[] (техзадание docx: file_path+file_name), winner_id/winners_id (для закрытых — результаты!).
Вывод: ПОДТВЕРЖДЕНО — позиции только в detail. GET, анонимно. Латентность выше etender (~2.3s vs 1.3s).
## 7. xarid GetCompetition — латентность НЕСТАБИЛЬНА (критично для краула!)
Замеры (1 rps, sleep 1): id=22477 2.29s | id=22476 5.45s | id=22475 ТАЙМАУТ 30s → ретрай OK 8.11s | id=22471 20.44s
Все ответы 200, размер ~3 КБ. js_details везде заполнен (product_name+price+qty+customer).
Образцы: 22476 "Услуга...офисным вспомогательным персоналом" 45.7M (Biznesni rivojlantirish banki); 22471 "Услуга по подбору кадров" qty 6 46.3M (TURON BANK).
Вывод: эндпоинт работает, но p50~5s, p95~20-30s, бывают таймауты → нужен retry с бэкофом + параллелизм 2-3 коннекта, timeout >= 35s. Бюджет краула считать по ~5s/запрос, НЕ по 1s.
## 8. etender: полный list одной страницей + print-лоты СЕЙЧАС
POST TradeList {"from":0,"to":700,...} → 200, TIME 6.17s, SIZE 445 КБ, ВСЕ 663 лота ОДНИМ запросом (пагинация from/to без капа на 663).
Grep по name (печат|чоп эт|bosma|журнал|блокнот|бланк...) → 10 print-хитов, среди них:
- 494752 | bosma nashrdagi kitoblar sotib olish (книги) | 78.2M | до 2026-06-11
- 495282 | «HAMSHIRA» журналини чоп этиш | 227.8M | до 2026-06-15
- 490230 | газеты "Yangi O'zbekiston"/"Pravda Vostoka" печать на год | 6.3 МЛРД | до 2026-06-16
- 495594 | Guliston/Teatr jurnallari chop etish | 168.3M | до 2026-06-16
- 495922 | "O'zbekiston" jurnali 2-son | 60M | до 2026-06-17
- 495796 | Guliston va teatr 3-4-son | 398.2M | до 2026-06-17
Вывод: name в etender ЧАСТИЧНО ловит печать (когда заказчик пишет понятно), но узбекская латиница/кириллица вперемешку → keyword-набор должен покрывать оба алфавита. Для полноты всё равно нужен detail (budget_products[].Product_Code по ЕНКТ 17.23.*/58.*).
## 9. etender detail print-лота 495282 (HAMSHIRA журнал) — спецификация ПОДТВЕРЖДЕНА
GET GetTrade/495282/0 → 200, 1.35s, 36.9 КБ.
budget_products: Product_Code=18.11.10.000-00001 "Услуга по печатанию журнала", Quantity=54246 шт, Price=4200 сум/шт (Cost 227.8M).
Вывод: detail даёт ТОЧНЫЙ ЕНКТ-код печати (18.11.* = печать газет/журналов, 17.23.* = канцтовары бумажные, 58.* = издательские услуги) → надёжный фильтр print-лотов по Product_Code префиксам вместо хрупких keyword'ов.
## 10. xarid: ВЕСЬ list 4254 одним запросом + распределение категорий
POST GetCompetitions {"from":0,"to":4300} → 200, TIME 2.40s, SIZE 1.6 МБ, все 4254 строки ОДНИМ запросом (NB: offset cap 3000 как на /rpc тут НЕ применяется).
Категорий всего 31, распределение: 3134 (74%!) = "Услуги профессиональные, научные и технические, прочие" — мусорная категория, фильтрация по category_name бесполезна.
Print-смежные: "Услуги издательские" = 2 лота (id 21265 cost 6.7M, id 20886 cost 12.75M — НО end_date 2026-04-10 и 2026-03-12, т.е. ПРОСРОЧЕНЫ); "Услуги рекламные..." = 33 лота.
ВАЖНО: list содержит лоты с истёкшим end_date_submitting_offers → "4254 активных" включает закрытые/архивные; реальная дельта в день сильно меньше.
## 11. xarid: "4254 активных" — МИФ. Реально открытых 28!
Анализ /tmp/xarid_all.json: лотов с end_date_submitting_offers >= 2026-06-11 всего 28 из 4254. Остальное — архив (list не фильтрует по статусу, отсортирован по id desc, свежие сверху).
Распределение дедлайнов открытых: 06-11: 3, 06-12: 11, 06-13: 1, 06-15: 9, 06-16: 4. Дневная дельта новых конкурсов ~5-15.
Detail закрытого лота 21265 (издательские): status="Совершен", js_details: "Услуга по публикации статьей в местном издательстве" 6.7M, winners_id=[{fullname, inn, pinfl, job_title}] — РЕЗУЛЬТАТЫ С ПОБЕДИТЕЛЕМ доступны анонимно! (intel-канал по конкурентам)
Вывод: detail-fetch нужен НЕ для 4253, а для ~28 открытых + ~10/день новых. Проблема стоимости краула отпадает.
## 12. etender: все 663 реально открыты, дневная дельта ~100-130
end_date >= сегодня: 663/663 (type=1 в TradeList отдаёт только активные — в отличие от xarid GetCompetitions).
Дедлайны: 06-11:120, 06-12:118, 06-15:109, 06-16:137, 06-17:94 ... хвост до 06-26.
Старты по дням: 06-04:105, 06-05:108, 06-08:106, 06-09:134, 06-10:93 → дельта ~100-130 новых тендеров/день (будни).
Стоимость detail-краула etender: бэкафилл 663 × ~1.3s ≈ 15 мин @1rps; дневная дельта ~120 × 1.3s ≈ 3 мин/день. Тривиально.
Стоимость xarid: бэкафилл 28 открытых × ~5s ≈ 2.5 мин; дельта ~10/день ≈ 1 мин/день (с retry/timeout 35s).
## 13. etender detail 490230 (газеты, 6.3 млрд) — мульти-позиционный лот
GET GetTrade/490230/0 → 200, 1.87s, 33 КБ. 2 позиции, обе Product_Code=18.12.19.190-00001 "Полиграфические услуги", qty 12 (мес), price 502.6M и 22.4M.
Вывод: budget_products бывает многострочным; Product_Code 18.12.* = прочие полиграфические услуги — добавить в фильтр-префиксы.

## ИТОГ
1. ОБА detail-endpoint'а ПОДТВЕРЖДЕНЫ анонимно (Chrome UA достаточно, куки/токены НЕ нужны):
   - etender: GET https://apietender.uzex.uz/api/common/GetTrade/{id}/0 → budget_products (СТРОКА с JSON! двойной parse) → Product_Name, Product_Code (ЕНКТ с дефисом), Quantity, Price, Description, Js_Properties; + contacts, файлы ТЗ, критерии js_fields. Латентность стабильная 1.2-1.9s, 28-40 КБ.
   - xarid: GET https://xarid-api-purchase.uzex.uz/Common/GetCompetition/{id} → js_details[] (настоящий массив) → product_name, description, quantity, price, js_properties; + customer_inn/phone, organizer_name (ЛИЧНЫЙ контакт), js_files ТЗ, winners_id (победители с ИНН/ПИНФЛ у закрытых). Латентность НЕСТАБИЛЬНА: 1.2-20s, бывают таймауты 30s → timeout 35-60s + 1 retry.
2. Гипотеза о корне "5669+4717 строк → 0-2 алерта" ПОДТВЕРЖДЕНА: etender list несёт только name (узб., часто общая фраза), xarid list — ВООБЩЕ без названия (только широкая категория, 74% лотов в одной мусорной категории). Server-side фильтр product_name в TradeList игнорируется. Предмет закупки существует ТОЛЬКО в detail.
3. Объёмы НЕ страшные: etender 663 активных (все живые), дельта ~100-130/день; xarid из 4254 в list реально открыты ТОЛЬКО 28 (остальное архив), дельта ~5-15/день. Бэкафилл: 663×1.3s + 28×5s ≈ 17 мин @1rps. Дневной инкремент: ~120×1.3s + 10×5s ≈ 3-4 мин/день.
4. Стратегия: state-файл max_seen_id per host → каждый краул тянуть list (1 запрос, отдаёт ВСЁ одной страницей: etender to=700, xarid to=4300) → detail только для id > max_seen_id (+ ретраи хвоста). Фильтр print: Product_Code префиксы 17.23./18.11./18.12./58. + keyword fallback по Product_Name/Description (кир+лат узб).
5. Print-лоты СЕЙЧАС (etender): 495282 журнал HAMSHIRA 227.8M (4200/шт × 54246); 490230 газеты 6.3 млрд (18.12.19.190); 495594 Guliston/Teatr 168.3M; 495922 O'zbekiston jurnali 60M; 494752 книги 78.2M; 495796 398.2M.
