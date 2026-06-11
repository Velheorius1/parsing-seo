# Аудит deep-links — кластер INDUSTRY (uzkimyo, uzsm, uzbekistonmet)
Дата: 2026-06-11. Аудитор: subagent corp-industry.

## Конфиги (sources.yaml)

### uzkimyo — "Узкимёсаноат" (строки 1776-1797)
- adapter: html, enabled: true
- url: https://uzkimyosanoat.uz/ru/press/tender
- selectors: container=`div.article-view`, title=`h3`, link=`h3 a@href`, deadline=""
- source_url_template: `https://uzkimyosanoat.uz{link}`

### uzsm — "УЗСМ (Металлургия)" (строки 1799-1820)
- adapter: html, enabled: true
- url: https://uzsm.uz/ru/activities/tenders/all/
- selectors: container=`div.tenders-list div.item`, title=`h3 a`, link=`h3 a@href`, deadline=`div.caption`
- source_url_template: `https://uzsm.uz{link}`

### uzbekistonmet — "Узбекистон металлургия комбинати" (строки 2161-2182)
- adapter: html, enabled: true, id_prefix: uzmet
- url: https://www.uzbekistonmet.uz/ru/lists/category/14
- selectors: container=`.news_box`, title=`a`, link=`a@href`, deadline=`ul.date_time li`
- source_url_template: `https://www.uzbekistonmet.uz{link}`

## БД (tenders, snapshot 2026-06-11)
| source | rows | last collected_at |
|---|---|---|
| Узкимёсаноат | 1 (!) | 2026-06-11 10:00:08 |
| УЗСМ (Металлургия) | 14 | 2026-06-11 10:00:09 |
| Узбекистон металлургия комбинати | 25 | 2026-06-11 10:00:08 |

Все три собирались сегодня — краулер живой по всему кластеру.

Samples из БД:
- uzkimyo: external_id=`uslugi-na-razrabotku-proekta-ekologicheskih-normativov-prede` → https://uzkimyosanoat.uz/ru/press/tender/uslugi-na-razrabotku-proekta-ekologicheskih-normativov-prede (slug выглядит обрезанным на "prede" — проверить)
- uzsm: `tender-na-postavku-uslugi` → https://uzsm.uz/ru/activities/tenders/all/tender-na-postavku-uslugi/
- uzsm ПОДОЗРИТЕЛЬНЫЕ external_id: `5` (АО Кварц, слаг кончается на "-doma-"), `2018` — экстрактор id, видимо, берёт цифры; риск коллизий dedupe
- uzbekistonmet: `7313` → https://www.uzbekistonmet.uz/ru/lists/view/7313

## uzkimyo — проверка живьём (2026-06-11)
- Листинг https://uzkimyosanoat.uz/ru/press/tender → HTTP 200, 45KB, без антибота.
- Sample deep-link (.../uslugi-na-razrabotku-proekta-ekologicheskih-normativov-prede) → HTTP 200, title лота найден на странице (2 вхождения). Слаг "prede" — НЕ обрезка краулером, это родной слаг сайта. **DIRECT_OK**.
- Негативный тест /ru/press/tender/nonexistent-slug-xyz-123 → HTTP 404 (честный, не редирект). Чисто.
- **БАГ (coverage, не deep-link):** container=`div.article-view` матчит ОДИН wrapper всего листинга (вёрстка плоская: h1, затем повторы h3+div.mime+p+hr без per-item обёртки). Адаптер итерирует контейнеры → извлекается ТОЛЬКО первый h3. На листинге 5 тендеров, в БД за всю историю 1 строка. 4/5 лотов теряются каждый прогон.
- **FIX (проверен на живом HTML, 5/5):**
  ```yaml
  html_selectors:
    container: "div.article-view h3"
    title: "a"
    deadline: ""
    link: "a@href"
  ```
  source_url_template без изменений (`https://uzkimyosanoat.uz{link}` — href относительный, начинается с /ru/...).

## uzsm — проверка живьём (2026-06-11)
- Листинг https://uzsm.uz/ru/activities/tenders/all/ → HTTP 200, 125KB. Селекторы валидны: `div.tenders-list div.item` = 15 контейнеров, h3 a и div.caption работают.
- Sample deep-link .../tender-na-postavku-uslugi/ → HTTP 200; title лота "Тендер на поставку услуги" в `<title>` и `<h1>`. **DIRECT_OK**.
- Негативный тест .../nonexistent-tender-xyz-2026/ → HTTP 404 (честный). Чисто.
- **НАХОДКА (staleness):** листинг — архив. Даты публикаций 2017–2020, новейшая 02.09.2020. Источник технически жив, но новых тендеров ~6 лет нет. Пагинации нет (15 итемов = всё). Ценность мониторинга ~0; кандидат на enabled:false или поиск нового раздела закупок ассоциации.
- **БАГ (dedupe, общий для html-адаптера):** ext_id = `re.search(r"(\d+)", link)` — ПЕРВОЕ число из слага. "fundamenta-5-etazhnogo" → ext_id=`5`; "prinadlezhnosti-2018" → `2018`. Коллизия: любой другой слаг с первым числом 5 → тот же uzsm-5 → молчаливый dedupe-дроп. FIX: цифровой ext_id только если число — отдельный сегмент/параметр (`r"/(\d+)/?$"` или `r"[?&]id=(\d+)"`), иначе полный слаг.

## uzbekistonmet — проверка живьём (2026-06-11)
- Листинг https://www.uzbekistonmet.uz/ru/lists/category/14 → HTTP 200, 49KB. Селекторы валидны: `.news_box` = 20 контейнеров, `a@href` и `ul.date_time li` работают. Листинг СВЕЖИЙ (итемы 08.06.2026, 05.06.2026...).
- Свежие id 8854/8850/8797/8760/8521 ВСЕ в БД (collected 2026-06-11) — пайплайн подхватывает новое без задержки.
- Sample deep-link /ru/lists/view/7313 → HTTP 200, `<title>` = "Внимание! Объявление о проведении тендера!" = title из БД. **DIRECT_OK**.
- Негативный тест /ru/lists/view/99999999 → HTTP 404, `<title>` "Страница не найдена". Честный 404. Чисто.
- Замечание (не баг): сервер медленный, полная загрузка деталки может занимать >25s (curl exit 28 при timeout 25; конфиг краулера timeout: 20 — но HTML head с title приходит рано, парсингу листинга хватает). При флапах таймаутов поднять timeout до 30.
- Фиксы не нужны.

## ИТОГ
| source | verdict | fix |
|---|---|---|
| uzkimyo (Узкимёсаноат) | DIRECT_OK (deep-link), но COVERAGE-БАГ: 1/5 лотов | container: `div.article-view h3`, title: `a`, link: `a@href` (проверено живьём, 5/5) |
| uzsm (УЗСМ Металлургия) | DIRECT_OK; источник-архив (последний тендер 09.2020) | deep-link фикс не нужен; рассмотреть enabled:false (мёртвый по контенту) + общий фикс ext_id (первое число из слага → коллизии) |
| uzbekistonmet (УзМет комбинат) | DIRECT_OK, источник полностью здоров | не нужен |

Кросс-катящий баг html-адаптера (html.py ~line 231): `re.search(r"(\d+)", link)` извлекает первое число ИЗ СЕРЕДИНЫ слага → uzsm ext_id `5`, `2018` → риск dedupe-коллизий на всех slug-источниках. Предложение: брать число только из конца пути (`r"/(\d+)/?$"`) или query (`r"[?&]id=(\d+)"`), иначе полный слаг.
