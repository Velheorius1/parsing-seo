# Чекпоинты: кластер intl (giz-tenders, osce-uz) — 2026-06-11

## Конфиги (sources.yaml)
- **giz-tenders** (строка 2257): name="GIZ Ausschreibungen", adapter=html, enabled=true.
  url=https://ausschreibungen.giz.de/Satellite/company/announcements/categoryOverview.do
  container="table.list tbody tr, .tender-row"; title="td:nth-child(2) a, .tender-title"; deadline="td:nth-child(4), .tender-deadline"; link="a@href"
  source_url_template="https://ausschreibungen.giz.de{link}"
- **osce-uz** (строка 451): name="OSCE Uzbekistan", adapter=html, enabled=true.
  url=https://procurement.osce.org/tenders?f[]=source:OSCE+Project+Co-ordinator+in+Uzbekistan
  container="article.node"; title="a"; deadline="time"; link="a@href"
  source_url_template="https://procurement.osce.org{link}"

## БД (tenders, read-only)
- **GIZ Ausschreibungen**: 0 строк (подтверждено SQL-запросом, `[]`).
- **OSCE Uzbekistan**: последние 2 строки:
  - external_id=`0`, title="Development, Installation, and Delivery of a Fully Functional and Tested Informa…", source_url=…/tenders/development-installation-and-delivery-fully-functional-and-tested-information-system-0, collected_at=2026-04-23 12:00
  - external_id=`development-installation-and-delivery-fully-functional-and-tested-information-system`, тот же title, collected_at=2026-04-16 10:00
  - ⚠️ Замечание: external_id="0" — суффикс `-0` слага съел id-экстрактор? Похоже id берётся из последнего сегмента URL → дубль одного лота под двумя id.

## GIZ — диагностика
- `curl GET https://ausschreibungen.giz.de/Satellite/company/announcements/categoryOverview.do` (как в конфиге) → **HTTP 400**, body = страница «Es ist ein Fehler (400) aufgetreten!» (cosinex Struts: dispatch-action без параметра `method`).
- Сайт ЖИВ (ответ за 0.17s, полноценный layout, jsessionid выдаётся), антибота нет.
- В навигации страницы найдена правильная ссылка листинга: `/Satellite/company/announcements/categoryOverview.do?method=showCategoryOverview` → в конфиге ПОТЕРЯН query-параметр `?method=showCategoryOverview`.

## GIZ — рабочий листинг найден (проверено живым curl)
- `?method=showCategoryOverview` → HTTP 200, но это НЕ листинг лотов, а таблица CPV-категорий («Auftragsgegenstand | Anzahl»), ссылки javascript:void(0) → старые селекторы конфига тут ничего не дадут.
- **Реальный листинг лотов:** `GET /Satellite/company/announcements/categoryOverview.do?method=showTable&cpvCode=<CPV>` → HTTP 200 БЕЗ cookies (cold-проверено), таблица `table.csx-new-table`, 20 строк-лотов.
  Колонки: 1=Veröffentlicht, 2=Frist (deadline), 3=Bezeichnung (title+link), 4=Typ, 5=Ausschreibende Stelle.
  Ссылка лота: `/Satellite/public/company/projectForwarding.do?pid=51020` (например «10023247 - Legal Advisory Services on Migration...», frist 17.06.2026).
- `method=showTable` БЕЗ cpvCode → отдаёт обратно категории (не «все лоты»). POST `method=search` с пустым searchText → 0 лотов. Значит обход = итерация по cpvCode (21 категория верхнего уровня из overview, формат NN000000-D).

## GIZ — deep-link верификация
- `projectForwarding.do?pid=51020` → 302 → `/Satellite/public/company/project/CXTRYY6YT2SPWDR4/de/overview` → HTTP 200, `<title>` = «CXTRYY6YT2SPWDR4 | 10023247 - Legal Advisory Services on Migration, Return and Reintegration | ausschreibungen.giz.de» — title лота присутствует. DIRECT_OK для шаблона `https://ausschreibungen.giz.de{link}` где link=`/Satellite/public/company/projectForwarding.do?pid=N`.
- Негативный тест: `pid=99999999` → HTTP 404, body 0 байт. Чисто.
- Структура листинга showTable: 2 таблицы `csx-new-table` (первая = лоты 20 шт + thead, вторая = 8 tr без ссылок). Пагинация — onclick JS (`selectedTablePagePROJECT_RESULT=2`), href-пагинации нет → crawler возьмёт только стр.1 (20 лотов на CPV) — для алертов достаточно.
- Адаптер html = BeautifulSoup soup.select (soupsieve, CSS4 ок) + next_page-селектор (тут бесполезен, JS).
- ⚠️ Попутно: external_id извлекается как ПЕРВОЕ `\d+` из link → для GIZ link `?pid=51020` даст чистый id `51020` — ок.

## OSCE — диагностика (начало)
- Конфиг-URL с фасетом `f[]=source:OSCE+Project+Co-ordinator+in+Uzbekistan` → HTTP 200 за 0.26s, h1 «All open tenders», но 0 ссылок `/tenders/` в body.
- Доступные значения фасета source на живой странице: Ashgabat, BiH, Serbia, Albania, Dushanbe, Secretariat — **«OSCE Project Co-ordinator in Uzbekistan» в фасетах ОТСУТСТВУЕТ** → у узбекского офиса сейчас 0 открытых тендеров (фасеты Drupal показывают только непустые значения). Молчание с 23.04 похоже на легитимное «нет лотов», НЕ на сломанный селектор. Проверяю селектор на нефильтрованном листинге.
- ⚠️ БД-аномалия объяснена кодом: `re.search(r"(\d+)", link)` в html.py берёт первую цифру из слага → slug `...-system-0` дал external_id="0" → один лот записан дважды (slug-id и "0"). Риск коллизий id между разными лотами с цифрой в слаге.

## GIZ — финальные селекторы (валидированы BeautifulSoup на живом HTML /tmp/giz_cold.html)
Структура строки лота (6 td): 1=Veröffentlicht, 2=Frist, 3=Bezeichnung (ЧИСТЫЙ ТЕКСТ, не ссылка!), 4=Typ, 5=Stelle, 6=Aktion (содержит `<a href="/Satellite/public/company/projectForwarding.do?pid=N">`).
- container `table.csx-new-table tbody tr` → 27 строк (20 лотов + 7 строк второй таблицы «Weitere Einschränkungsmöglichkeiten» с 2 td — отсекаются guard'ом адаптера: у них нет td:nth-child(3) → title пустой → skip).
- title `td:nth-child(3)` (старый `td:nth-child(2) a` не работает: текст не в ссылке и не во 2-й колонке).
- deadline `td:nth-child(2)`.
- link `a[href*=projectForwarding]@href` (rsplit("@",1) в _extract_field парсит корректно).
- Проверка: 20/20 строк дают title+link. external_id из `?pid=51020` → re (\d+) даст `51020` — чисто.
- Ограничение: SourceConfig.url — одна строка; loop (productName_param) есть только в api-адаптере → для нескольких CPV нужны отдельные yaml-блоки (или расширять html-адаптер).

## OSCE — финал
- Нефильтрованный листинг https://procurement.osce.org/tenders → 10 лотов, `article.node` матчит все 10 (`<article class="node node--type-tender node--view-mode-solr-search-result teaser">`), ссылки `/tenders/<slug>` внутри `h4 a` — селекторы конфига РАБОЧИЕ.
- Фасет-синтаксис жив: `?f[]=source:OSCE+Presence+in+Albania` → 1 лот Albania, facet item active. Формат конфига валиден.
- Sample deep-link из БД (…information-system-0) → HTTP 200, h1 = «Development, Installation, and Delivery of a Fully Functional and Tested Information System» (совпадает с title в БД). Негатив: несуществующий slug → 404.
- Вывод: молчание с 23.04 = у «OSCE Project Co-ordinator in Uzbekistan» нет открытых тендеров (значение отсутствует в живом списке фасетов = 0 лотов). Источник НЕ сломан.

## ИТОГ
| Источник | Вердикт | Причина | Фикс |
|---|---|---|---|
| giz-tenders | WRONG_PAGE | Конфиг-URL без `?method=…` → HTTP 400 (страница ошибки cosinex), 0 строк в БД. Даже с `method=showCategoryOverview` это страница CPV-категорий, не лоты | url → `…/categoryOverview.do?method=showTable&cpvCode=79000000-4` (и доп. блоки на нужные CPV); селекторы: container `table.csx-new-table tbody tr`, title `td:nth-child(3)`, deadline `td:nth-child(2)`, link `a[href*=projectForwarding]@href`; шаблон `https://ausschreibungen.giz.de{link}` оставить. Проверено: 20/20 лотов, deep-link pid→302→overview c title, негатив pid → 404 |
| osce-uz | DIRECT_OK | Конфиг, селекторы, фасет и deep-link рабочие (проверено на живом листинге и Albania-фасете). Тишина с 23.04 = легитимно нет открытых лотов узбекского офиса | не нужен. (Минорно, отдельно от источника: html.py `re.search(r"(\d+)", link)` берёт первую цифру слага → external_id="0" для slug `…-system-0`, дубль лота; лучше брать полный последний сегмент slug'а) |
