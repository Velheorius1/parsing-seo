# Верификация канала ref_request_proposals_public (api.xt-xarid.uz/rpc)
Дата: 2026-06-11. Reverse-verifier, только чтение. Адаптер-образец: xt-xarid-reduction (sources.yaml:2420).

## 1. Воспроизведение curl — ПОДТВЕРЖДЕНО
curl -s -X POST 'https://api.xt-xarid.uz/rpc' -H 'Content-Type: application/json' \
  -d '{"id":1,"jsonrpc":"2.0","method":"ref","params":{"ref":"ref_request_proposals_public","op":"read","limit":2,"offset":0}}'

Ответ HTTP 200, exit 0. Структура записи (как заявлено):
- type:"request_proposals", status:"open", remain_time (сек до закрытия), publicated_at, close_at
- name (заголовок RFP), totalcost, currency:"UZS"
- company_name, company_inn, company_id (в корне И в meta)
- meta.good_maps[] = массив товаров: {name, price, amount, unit, id (KTRU-код), totalcost_item, lot_id}
- meta.lots[], meta.fin_src[], good (JSON-строка списка id), good_count, lot_count
- id (proc_id для deep-link), part_count, green, multilot

Пример живой записи: id=7651976 "Ось, колесо цельнокатаное..." (O'ztemiryolekspeditsiya, ИНН 201534405), 5 товаров, totalcost 14.7 млрд, close 2026-06-12.
ВЫВОД: канал жив, отдаёт реальный СПРОС покупателей (RFP) с полным good_maps. Структура совпадает с заявленной.

## 2. Объём активных — ПОДТВЕРЖДЕНО (низкий)
Сортировка по умолчанию: новейшие сверху. Лента СМЕШАННАЯ — open + close + cancel + check_proposals + not_realized.
Первые 100 записей: open=15, check_proposals=9, not_realized=3, close=59, cancel=14 (publicated 2026-05-22 → 2026-06-10).
Server-side фильтр РАБОТАЕТ: filters:{"status":"open"} → ровно 15 строк (limit 100 тоже даёт 15 = это ПОЛНЫЙ объём активных RFP по ВСЕЙ платформе сейчас).
OKED-префиксы в 15 открытых good_maps: 28.13/31.01/29.32/30.20/28.11 (насосы, ж/д, медтехника, машиностроение). Префиксов 17.* / 18.1 (полиграфия) среди открытых СЕЙЧАС — НЕТ.
ВЫВОД: канал низкообъёмный по спросу (десятки активных всего, единицы новых в день). Полиграфия проходит редко — это широкий канал спроса по ВСЕМ товарам, печать = малая доля.

## Подводный камень: лимит + burst-throttle
- limit MAX = 100 на запрос (limit>100 → error code bad_params "Значение должно быть <= 100").
- ВАЖНО: при серии быстрых запросов (~6+ подряд) сервер начинает возвращать ТОТ ЖЕ error "limit должно быть <= 100" ДАЖЕ на валидный limit=2. Это маскированный rate-limit/WAF, НЕ реальная проблема лимита. Восстанавливается за секунды при паузе. => адаптеру нужен rate_limit ~2 req/s + retry на code:bad_params.

## 3. PRINT-релевантные ЖИВЫЕ записи (скан offset 0-600, 369 unique procs)
Сейчас (open=15) полиграфии НЕТ. В историческом окне (~2 мес назад) найдено 5 print-хитов (статусы close/cancel — окно отдаёт смесь):
- 7074727 | close | 2026-04-08 | 4 000 000 UZS | "Гувоҳнома бланки" (KTRU 17.23.13.140_00001 = бланки/сертификаты, ПРЯМАЯ полиграфия) | Персонални тайёрлаш муассаса
- 6899163 | close | 2026-04-02 | 68 500 000 UZS | "Иш журнали" (журнал учёта, 58.14.12.000_00003) | "Dorkomplektsnab servis" MCh
- 7239218 | close | 2026-04-29 | 481 155 392 UZS | "Услуга по подписке и доставке периодического печатного издания" (58.14.19) | Ташкентский гос.
- 6881677 | close | 2026-03-17 | 80 574 060 UZS | то же издание | Ташкентский гос.
- 7150065 | cancel | 2026-04-29 | то же | Ташкентский гос.
ВЫВОД: канал ЛОВИТ полиграфию (бланки 17.23.*, журналы учёта). Но поток РЕДКИЙ — ~1-2 print-RFP в месяц. Все найденные сейчас закрыты (окно историческое). Печать = малая доля широкого канала спроса.

## 4. Deep-link + get_proc enrichment — ПОДТВЕРЖДЕНО
- Полная карточка: POST https://api.xt-xarid.uz/urpc {"id":1,"jsonrpc":"2.0","method":"get_proc","params":{"proc_id":7074727}}
  → result.fields{} с totalcost, tech_task_additional_files, sources_of_finance, sum_of_positions_exchanges (мультивалюта), и т.д. ENRICHMENT работает.
- Web deep-link: https://xt-xarid.uz/procedure/{id}/core → HTTP 200 (оба теста: 7074727, 7651976). SPA-shell, данные гидратятся тем же JSON-RPC, но URL валиден.

## 5. Точная структура полей (для field_map)
ROOT keys: area, close_at, company_id, company_name, contract_id, contract_number, currency, good, good_count, green, id, is_new_multilot, lang, lot_count, meta, multilot, name, part_count, publicated_at, remain_time, status, totalcost, type
- id = proc_id (для deep-link и get_proc)
- name = заголовок RFP (title)
- company_name = заказчик (org). ВНИМАНИЕ: company_inn в КОРНЕ часто null → брать meta.company_inn
- totalcost = стартовая сумма (price), currency = "UZS"
- close_at = дедлайн, publicated_at = дата публикации, remain_time = сек до закрытия (>0 = активно)
- status: open / check_proposals / not_realized / close / cancel
- area = регион, но ОБЫЧНО null (не использовать как обязательный)
meta keys: area_path, company_inn, company_name, fin_src, good_maps[], lots[]
good_maps[i] keys: {name, price, amount, unit, id (KTRU/OKED-код для print-фильтра), totalcost_item, lot_id}

## ИТОГ
КАНАЛ ПОДТВЕРЖДЁН — holds=true. ref_request_proposals_public на api.xt-xarid.uz/rpc жив, анонимный, отдаёт реальный СПРОС покупателей (RFP) с полным good_maps[] (товар+цена+кол-во+KTRU-код), company_name/inn, totalcost, статусы.

Объём: лента смешанная (open+archive). Активных open по всей платформе СЕЙЧАС = ровно 15 (server-side filter status:open). Новых в день — единицы. offset cap 3000, limit cap 100/запрос.

Print-релевантность: НИЗКАЯ-СРЕДНЯЯ. Прямая полиграфия (бланки 17.23.*, журналы учёта, печатные издания 58.14.*) проходит ~1-2 RFP/мес. Сейчас в open — нет, но в 2-мес окне найдено 5. Фильтр client-side по good_maps[].name (блокнот/бумаг/печат/бланк/журнал) + good_maps[].id prefix (17.23/17.12/18.1/58.14).

Deep-link: xt-xarid.uz/procedure/{id}/core = HTTP 200. Enrichment: POST /urpc get_proc {proc_id} = полная карточка fields{}.

Подводные камни:
1. limit MAX 100. burst-throttle маскируется под error "limit <= 100" даже на limit=2 → rate_limit ~2 req/s + retry на code:bad_params.
2. company_inn в корне null → fallback meta.company_inn.
3. area почти всегда null → не делать region обязательным.
4. Лента отдаёт архив (close/cancel ~75%) → нужен item_filter по status (как у xt-xarid-tender).
5. price=totalcost (сумма всей процедуры), а не за единицу.

Рецепт адаптера (по образцу xt-xarid-reduction, sources.yaml:2420):
  - id: xt-xarid-request-proposals
    adapter: jsonrpc
    url: "https://api.xt-xarid.uz/rpc"
    rpc_ref: "ref_request_proposals_public"
    rpc_method: "ref"
    rate_limit: 2.0
    id_prefix: "xtx-rp"
    dedup_group: "xtx-request-proposals"
    field_map:
      title: "name"
      organization: "company_name"
      price: "totalcost"
      currency: "currency"
      deadline: "close_at"
      external_id: "id"
      source_url_template: "https://xt-xarid.uz/procedure/{external_id}/core"
    keywords_fields: ["name", "meta.good_maps"]
    pagination: { type: offset, param: offset, page_size: 100, max_pages: 5 }
    item_filter:
      status: { in: [open, check_proposals] }
