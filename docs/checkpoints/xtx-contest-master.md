
# ===== СЕССИЯ 2026-06-11: ref_contest_public + ref_master_agreement_public (api.xt-xarid.uz) =====

## 1. ref_contest_public — базовый read (аноним)
curl -s 'https://api.xt-xarid.uz/rpc' -H 'Content-Type: application/json' -d '{"id":1,"jsonrpc":"2.0","method":"ref","params":{"ref":"ref_contest_public","op":"read","limit":2,"offset":0}}'
Результат: 200 OK, JSON result[] из записей type="contest".
Первая запись: id=14058, "Торжественное открытие нового офиса ГУП UNICON.UZ", totalcost=243млн UZS, status=close, publicated_at=2021-07-20, part_count=3.
Поля: type, totalcost, status, remain_time, publicated_at, part_count, name, meta.good_maps[{unit,totalcost_item,price,name,id(GUID),category_type,category_id,amount}], meta.fin_src, meta.company_name, meta.company_inn, meta.area_path, id, good_count, currency, contract_id, company_name, company_id, close_at, area.
ПРИМЕЧАТЕЛЬНО: good_maps[].id тут GUID (не OKED-код как в e-shop), category_id тоже GUID.
Вывод: ПОДТВЕРЖДЕНО — канал читается анонимно, дефолтная сортировка = от старых (2021) к новым.

## 2. ref_contest_public — объём и server-side фильтр status
curl ... '{"params":{"ref":"ref_contest_public","op":"read","limit":1,"offset":2999}}' → {"result":[]} — ПУСТО, значит всего записей < 3000.
curl ... '{"params":{...,"filters":{"status":"open"}}}' → 200 OK, работает server-side фильтр! Но первая запись open = ТЕСТОВАЯ 2020 года (OOO "Звездочка" (Тест), Комбикорм, 10000 UZS) — open включает мусорные стейл-записи.
Вывод: filters по status подтверждён server-side. Дефолтная сортировка ASC (старые первыми) — для свежих нужно читать с хвоста (offset = N-k) или искать сортировку.
Локально: crawler/adapters/jsonrpc.py фильтры НЕ шлёт (только limit/offset), фильтрация client-side через item_filter.

## 3. ref_contest_public — ТОЧНЫЙ ОБЪЁМ: 291 запись, канал ИСТОРИЧЕСКИЙ (мёртв с 2021)
Бинарный поиск: offset 2999/2000/1000/500/300 → пусто; offset 250 limit 100 → 41 строка => ВСЕГО = 291.
Сортировка: id DESC (новейшие первыми). Новейшая запись id=14058 publicated_at=2021-07-20.
Статусы в хвосте (41 шт): close=27, check_docs=9, cancel=3, stop=1, open=1 (open = тестовая запись 2020).
Вывод: НА api.xt-xarid.uz КОНКУРСЫ НЕ ПРОВОДЯТСЯ С ИЮЛЯ 2021. Активных нет. Канал = архив 2020-2021.

## 4. ref_contest_public — ПОЛНЫЙ СКАН всех 291 записей (3×limit=100)
Статусы: close=202, check_docs=45, cancel=40, commercial_checking=2, stop=1, open=1(тест).
Годы: 2021=262, 2020=29. Активных (реально открытых) = 0.
Print-релевантные записи (поиск по 30+ ключам в name+good_maps):
- #12570 | 2021-07-05 | close | 45,000,000 UZS | "Услуги по изготовлению годового отчета" / good: полиграфические услуги
- #23    | 2020-09-10 | check_docs | 10,000,000 UZS | Конкурс по позиции ПГЗ "Бумажный пакет" / good: бумажный пакет
- #14058 | 2021-07-20 | close | 243,000,000 UZS | "Торжественное открытие офиса UNICON.UZ" / good: изготовление и оформление интерьера (пограничный)
- #72    | 2020-10-26 | close | мебель (false positive по ключу "журнальный")
Итого реальных print-лотов за всю историю: 2-3 из 291 (~1%).
Вывод: канал ПОДТВЕРЖДЁН технически, но БЕСПОЛЕЗЕН для алертов — мёртв с 07.2021, 0 активных.

## 5. ref_master_agreement_public — базовый read (аноним)
curl -s 'https://api.xt-xarid.uz/rpc' -H 'Content-Type: application/json' -d '{"id":1,"jsonrpc":"2.0","method":"ref","params":{"ref":"ref_master_agreement_public","op":"read","limit":2,"offset":0}}'
Результат: 200 OK. Запись[0]: type=master_agreement, totalcost=1,142,906,731,868 UZS (1.14 трлн!), status=close, publicated_at=2022-10-27, part_count=8, name="Закупка крупногабаритных шин..."
Поля как у contest, НО: meta.lots[{total_sum_lot,lot_id,item_count}], good_maps богаче — id="22.11.14.191-00001" (ENKT/OKED-код!), category{code,translations ru/uz-Cyrl/uz-Latn,parent_id} + price/amount per позиция.
Вывод: ПОДТВЕРЖДЕНО — канал читается анонимно, good_maps несут классификатор-код => фильтрация полиграфии по префиксу кода (17.23.x, 58.x, 18.x) возможна client-side.

## 6. ref_master_agreement_public — ТОЧНЫЙ ОБЪЁМ: 1 (ОДНА) запись
offset 2999/500/100 → пусто; offset 0 limit 100 → ровно 1 строка.
Единственная запись: id=586401, 2022-10-27, status=close, totalcost=1.14 трлн UZS, "Закупка крупногабаритных шин..." (НГМК-масштаб), part_count=8, мульти-позиционные good_maps с кодами ЕНКТ 22.11.14.191.
Print hits: 0 (естественно — одна запись про шины).
Поля записи НЕ содержат победителя/поставщика — только company_name заказчика (meta.company_inn = ИНН заказчика).
Вывод: канал НЕ исторический архив, а ПОЧТИ ПУСТОЙ — 1 рамочник за всю историю площадки. Для конкурентной разведки по поставщикам в list-данных НИЧЕГО нет.

## 7. get_proc (urpc) для master_agreement #586401 — конкурентная разведка
curl -s 'https://api.xt-xarid.uz/urpc' -H 'Content-Type: application/json' -d '{"id":1,"jsonrpc":"2.0","method":"get_proc","params":{"proc_id":586401}}'
Результат: 200 OK анонимно, 42 КБ. top keys: actions, agreement_members, blocked_by, config, created_at, db, end_at, fields, gup, master_agreement_sign(GUID), master_agreement_signed(2022-12-23), my_role, objections, proc_id, procedure, requests, status, type.
- requests: [28 числовых id заявок участников] — но содержимое заявок анонимно НЕ раскрывается
- agreement_members: [] — ПУСТО для анонима (my_role=no_role)
- Победители/поставщики: НЕ ВИДНЫ. Единственное company_name = ЗАКАЗЧИК (АО "НГМК"). "choose_winner" — лишь UI-метаданные action
- Зато видны: закупочная документация (PDF-приложения с file id), стартовые цены по позициям, требования к участникам
Вывод: ОПРОВЕРГНУТО для конкурентной разведки — цены/поставщики ЗАКЛЮЧЁННЫХ рамочников анонимно НЕ видны (только стартовые цены заказчика + число участников part_count=8 + кол-во заявок=28).

## 8. Доп. проверки
a) get_proc {proc_id:586401, part_id:2053049} (id заявки участника) → результат ИДЕНТИЧЕН анонимному, my_role=no_role. Заявки участников закрыты авторизацией (E-IMZO).
b) api.hayotbirja.uz/rpc ref_master_agreement_public limit=1 → ТА ЖЕ запись id=586401 "Закупка крупногабаритных шин". ПОДТВЕРЖДЁН общий бэкенд hayotbirja=xt-xarid: краулить эти refs нужно ТОЛЬКО с одного хоста.

## ИТОГ (сессия 2026-06-11, ref_contest_public + ref_master_agreement_public @ api.xt-xarid.uz)

### ref_contest_public: ПОДТВЕРЖДЁН технически, ПУСТ практически
- Всего 291 запись, ВСЕ 2020-2021 (262 за 2021, 29 за 2020). Новейшая — 2021-07-20.
- Статусы: close=202, check_docs=45, cancel=40, commercial_checking=2, stop=1, open=1 (тестовая).
- Активных конкурсов: 0. Площадка не проводит конкурсы с июля 2021.
- Print-релевантных за всю историю: 2-3 (~1%): #12570 полиграфуслуги 45 млн, #23 бумажный пакет 10 млн, #14058 оформление интерьера 243 млн.
- Сортировка id DESC (новые первыми). filters server-side работает ({"status":"open"}).
- РЕКОМЕНДАЦИЯ: в краул НЕ добавлять (мёртвый канал). Если добавлять для полноты — однократный бэкфил 3 страницы × 100.

### ref_master_agreement_public: ПОДТВЕРЖДЁН технически, ПОЧТИ ПУСТ (1 запись)
- ВСЕГО 1 запись за всю историю: id=586401, НГМК, шины, 1.14 трлн UZS, 2022, close.
- Конкурентная разведка (вопрос сессии): ОПРОВЕРГНУТА. Анонимно НЕ видны ни победители, ни цены заключённого соглашения. Видны только: стартовые цены заказчика per-позиция (good_maps), part_count, число заявок (28 id в requests[]), дата подписания, закупочная документация (PDF). agreement_members=[] для анонима.
- РЕКОМЕНДАЦИЯ: в краул НЕ добавлять.

### Рецепт (если когда-нибудь оживут)
POST https://api.xt-xarid.uz/rpc, Content-Type: application/json, аноним (без UA-требований)
body: {"id":1,"jsonrpc":"2.0","method":"ref","params":{"ref":"ref_contest_public"|"ref_master_agreement_public","op":"read","limit":100,"offset":N,"filters":{"status":"open"}}}
Сортировка id DESC; offset cap 3000 (фактически не достигается). Карточка: POST /urpc {"method":"get_proc","params":{"proc_id":X}}. Deep-link: https://xt-xarid.uz/procedure/{id}/core
good_maps: contest = GUID-id товаров; master_agreement = коды ЕНКТ (22.11.14.191-00001) + category.code + translations ru/uz — print-фильтр client-side по префиксам 17.23/17.12/18.1/58.1.
hayotbirja.uz = тот же бэкенд (та же запись 586401) — НЕ дублировать краул.
Запросов потрачено: ~20 на api.xt-xarid.uz, 1 на api.hayotbirja.uz. Мутаций не было.
