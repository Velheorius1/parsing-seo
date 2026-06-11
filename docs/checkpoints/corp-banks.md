# Аудит deep-links — кластер banks (2026-06-11)

## Конфиги (sources.yaml)
- **nbu** (id=nbu, name="НБУ (Нац. банк ВЭД)", html, enabled): url=https://nbu.uz/ru/tendery, container=`a.newsfeed_01_news-item`, title=`div.newsfeed_01_news-title`, link=`@href`, template=`https://nbu.uz{link}`
- **sqb** (id=sqb, name="Узпромстройбанк (SQB)", html, enabled): url=https://sqb.uz/press-center/tenders-ru/, container=`.news-item`, title=`div.news-title a`, link=`div.news-title a@href`, template=`https://sqb.uz{link}`. Есть sqb-old (disabled, заменён).
- **aab** (id=aab, name="Asia Alliance Bank", html, enabled): url=https://aab.uz/ru/press_center/tenders/last/, container=`div.post`, title=`a.post__title`, link=`a.post__title@href`, template=`https://aab.uz{link}` — В БД 0 строк, диагностировать
- **anorbank** (id=anorbank, name="Anor Bank", html, enabled, use_proxy=true — 403 с VPS без прокси): url=https://www.anorbank.uz/about/press-center/tendery/, container=`div.news_list_wrapper_items`, title=`h3`, link=`a.news_list_wrapper_items_article@href`, template=`https://www.anorbank.uz{link}`
- **trustbank** (id=trustbank, name="Трастбанк", html, enabled): url=https://trustbank.uz/ru/press_center/tenders/last/, container=`div.tenders-list div.item`, title=`h3 a`, link=`h3 a@href`, template=`https://trustbank.uz{link}`

## БД (tenders, 2026-06-11)
| source | cnt | last_seen |
|---|---|---|
| Anor Bank | 20 | 2026-06-11 06:00 |
| НБУ (Нац. банк ВЭД) | 9 | 2026-06-11 10:00 |
| Трастбанк | 15 | 2026-06-11 10:00 |
| Узпромстройбанк (SQB) | 29 | 2026-06-11 10:00 |
| Asia Alliance Bank | **0** | — (подтверждено: ни одной строки) |

Сэмплы:
- Anor: ext=tekhnicheskoe-zadanie-...-informats, url=https://www.anorbank.uz/about/press-center/tendery/tekhnicheskoe-zadanie-na-okazanie-uslug-po-monitoringu-sotsialnykh-setey-i-sredstv-massovoy-informats/
- НБУ: ext=**"14"/"15" (числовые! хрупкие)**, title="...предложений14.05.2026База RIPE NCC" (склейка title+date+подзаголовка), url=https://nbu.uz/ru/tendery/ao-uznacbank-obieiavliaet-otbor-nailucsix-predlozenii14_05_2
- Трастбанк: ext="3" и "vnimanie-tenderlkjhglkj", title="Внимание, тендер!" (generic, без сути лота), url=https://trustbank.uz/ru/press_center/tenders/last/vnimanie-tender-%3Blkjhgfdsdfghjk/
- SQB: ext=9552, url=https://sqb.uz/press-center/tenders-ru/akb-uzpromstroybank-obyavlyaet-o-razrabotke-dolgosrochnoy-programmy-razvitiya-sektora-teplits9552/

## Живые проверки (Mac, 2026-06-11)
- nbu.uz — TCP connect timeout с Mac (185.104.210.5:443 не отвечает; видимо geo/файрвол). С VPS работает (БД пополнилась сегодня 10:00). Проверю через VPS.
- sqb.uz/press-center/tenders-ru/ — 200, 117KB
- **aab.uz/ru/press_center/tenders/last/ — 404 (350KB кастомная страница ошибки). ROOT CAUSE 0 строк: листинг переехал/удалён**
- trustbank.uz/ru/press_center/tenders/last/ — 200, 371KB
- anorbank.uz/about/press-center/tendery/ — 200 с Mac без прокси (637KB); 403 только с VPS (известный geo-block, поэтому use_proxy=true)

## AAB — диагностика 0 строк (ROOT CAUSE найден)
1. Конфиг-URL `/ru/press_center/tenders/last/` ("Прошедшие конкурсы") отдаёт **HTTP 404**, хотя body содержит 15 `post__title` — Bitrix рендерит контент с кодом 404. Краулер бракует не-200 → 0 строк.
2. Семантика тоже неверна: `/last/` = ПРОШЕДШИЕ тендеры. Действующие = `/ru/press_center/tenders/current/` → **HTTP 200**, 2 активных лота.
3. Селекторы конфига ЖИВЫ на /current/: `div.post` (2 шт), `a.post__title` (2 шт), `div.post__date span` (есть "Дата опубликования" в `.post__date.mr-10` и "Дата истечения" во втором `.post__date`).
4. FIX: поменять url на `https://aab.uz/ru/press_center/tenders/current/`, селекторы оставить. Для deadline лучше уточнить — см. вердикт.

## SQB — DIRECT_OK
- Листинг 200; container `.news-item` матчит `class="news-item w-100"` (18 шт), `div.news-title a` и `div.news-date` (10.06.2026 18:06) живы.
- Deep-link из БД (…teplits9552/) → 200, "теплиц" ×5 в body — страница лота настоящая.
- Негативный тест: bogus slug → **200 (soft-404!)** — битые ссылки кодом не ловятся, нужна проверка по title при валидации.

## Трастбанк — deep-link OK, но листинг = ПРОШЕДШИЕ тендеры
- Селекторы живы: `div.tenders-list div.item` (матчит `.news-list.tenders-list` > `.item.has-preview-text`, 15 шт), `h3 a`, `div.caption` (Дата опубликования/истечения).
- Deep-link из БД (`.../last/vnimanie-tender-%3Blkjhgfdsdfghjk/`) → 200, title совпадает; в body есть реальный предмет лота (DLP-лицензии). Bogus slug → 404 (чисто).
- ПРОБЛЕМА 1 (семантика): конфиг-URL `/tenders/last/` = прошедшие. Меню сайта: `/current/`=Действующие (сейчас пуст: "Информация отсутствует"), `/all/`=Все тендеры (12 шт, тендеры+конкурсы). Алерты по /last/ приходят ПОСЛЕ истечения срока.
- ПРОБЛЕМА 2 (качество): заголовки на сайте generic "Внимание, тендер!" + slug-мусор (lkjhgfdsdfghjk) — предмет лота только на detail-странице.
- ПРОБЛЕМА 3: смешанные external_id ("3" числовой vs "vnimanie-tenderlkjhglkj" слаговый) — риск дублей; числовой ext_id="3" сегодня собран по тендеру от 08.11.2024.
- FIX: url → `https://trustbank.uz/ru/press_center/tenders/all/` (надмножество; те же селекторы работают). Для предмета лота — фетчить detail и брать первый абзац `.js-content-block` (опционально).
# Аудит deep-links — кластер banks (2026-06-11)

## Конфиги (sources.yaml)
- **nbu** (строка 1680): name="НБУ (Нац. банк ВЭД)", html, enabled, url=https://nbu.uz/ru/tendery, container=a.newsfeed_01_news-item, title=div.newsfeed_01_news-title, link=@href, template=https://nbu.uz{link}
- **sqb** (строка 2138): name="Узпромстройбанк (SQB)", html, enabled, url=https://sqb.uz/press-center/tenders-ru/, container=.news-item, title=div.news-title a, link=div.news-title a@href, template=https://sqb.uz{link}. Есть sqb-old (1586, disabled)
- **aab** (строка 1632): name="Asia Alliance Bank", html, enabled, url=https://aab.uz/ru/press_center/tenders/last/, container=div.post, title=a.post__title, link=a.post__title@href, template=https://aab.uz{link}
- **anorbank** (строка 1655): name="Anor Bank", html, enabled, **use_proxy: true** (403 с VPS без прокси), url=https://www.anorbank.uz/about/press-center/tendery/, container=div.news_list_wrapper_items, title=h3, link=a.news_list_wrapper_items_article@href, template=https://www.anorbank.uz{link}
- **trustbank** (строка 1609): name="Трастбанк", html, enabled, url=https://trustbank.uz/ru/press_center/tenders/last/, container=div.tenders-list div.item, title=h3 a, link=h3 a@href, template=https://trustbank.uz{link}

Замечание: aab и trustbank имеют ОДИНАКОВЫЙ путь листинга /ru/press_center/tenders/last/ — похоже один и тот же CMS-движок, но разные селекторы (aab: div.post; trustbank: div.tenders-list div.item). Подозрение: один из двух конфигов писался под чужой сайт.

## БД (tenders, 2026-06-11)
| source | строк | last collected |
|---|---|---|
| Anor Bank | 21 | 2026-06-11 14:00 |
| НБУ (Нац. банк ВЭД) | 9 | 2026-06-11 18:00 |
| Трастбанк | 15 | 2026-06-11 18:00 |
| Узпромстройбанк (SQB) | 29 | 2026-06-11 18:00 |
| **Asia Alliance Bank** | **0** | — |

Сэмплы (последние 2):
- anorbank: ext=tekhnicheskoe-zadanie-na-kapitalnyy-remont... → https://www.anorbank.uz/about/press-center/tendery/tekhnicheskoe-zadanie-na-kapitalnyy-remont-pomeshcheniy-bukharskogo-op-/
- nbu: ext=**"14"/"15" (числовые! нестабильные id)**, title с конкатенацией даты+подзаголовка ("...предложений14.05.2026База RIPE NCC") → https://nbu.uz/ru/tendery/ao-uznacbank-obieiavliaet-otbor-nailucsix-predlozenii14_05_2
- trustbank: ext="3" и "vnimanie-tenderlkjhglkj" → https://trustbank.uz/ru/press_center/tenders/last/vnimanie-tender-%3Blkjhgfdsdfghjk/ (слаги — клавиатурный мусор от контентщиков банка, но это реальные слаги)
- sqb: ext=9552 → https://sqb.uz/press-center/tenders-ru/akb-uzpromstroybank-obyavlyaet-o-razrabotke-dolgosrochnoy-programmy-razvitiya-sektora-teplits9552/

## NBU — сеть
- С Mac nbu.uz (185.104.210.5) полный TCP-таймаут (60с, HTTP 000) — гео/сетевой фильтр на стороне НБУ для не-VPS сетей. НО краулер с VPS собрал строку сегодня 18:00 → для краулера сайт ЖИВ. Проверяю sample URL с VPS.

## Логи tender-crawler (VPS, 2026-06-11)
- **aab: HTTP 404** на https://aab.uz/ru/press_center/tenders/last/ (стабильно, 16:00 и 18:00) → "Fetched 0 items". ЛИСТИНГ ПЕРЕЕХАЛ ИЛИ НИКОГДА НЕ СУЩЕСТВОВАЛ (путь скопирован с trustbank.uz — тот же /ru/press_center/tenders/last/). Это причина 0 строк.
- **anorbank:** "Failed to fetch ... : " (пустой текст ошибки = timeout/proxy) в 16:00 и 18:00 → 0 items. Последний успешный сбор 14:00 (21 строка в БД). use_proxy=true — прокси флапает, источник работает через раз.
- **nbu:** 16:00 = 503 Service Unavailable; 18:00 = 200 OK, 6 tenders, маркер "REVIVED SOURCES: nbu". Сайт НБУ нестабилен (сейчас 18:30 — TCP-таймаут и с Mac, и с VPS), но краулер периодически пробивается.
- sqb: 18 items OK; trustbank: 15 items OK.

## AAB — ДИАГНОЗ (0 строк) + FIX (проверен на живом HTML)
- Корень: конфиг-URL https://aab.uz/ru/press_center/tenders/last/ = **HTTP 404** (раздел /last/ удалён/переименован). Сайт жив.
- Хаб https://aab.uz/ru/press_center/tenders/ (200) ссылается на: /tenders/all/ (15 постов + PAGEN_1 пагинация) и /tenders/current/ (2 активных тендера: XDR, WAF FortiWeb).
- Селекторы конфига ВАЛИДНЫ для новых страниц: container=div.post (15 шт на /all/, 2 на /current/), title=a.post__title (нашёл: `<a href="..." class="post__title">АКБ «Asia Alliance Bank» объявляет отбор...`), link=a.post__title@href (относительный → template https://aab.uz{link} ок), deadline=div.post__date присутствует.
- Deep-link проверен: https://aab.uz/ru/press_center/tenders/current/akb-asia-alliance-bank-obyavlyaet-otbor-na-vnedrenie-apparatnogo-kompleksa-web-application-firewall-/ → 200, текст "Web Application Firewall" на странице (12 вхождений).
- Негативный тест: /tenders/current/bogus-nonexistent-slug-12345/ → **404** (честный, не soft-200). ✓
- **FIX:** в sources.yaml id=aab поменять ТОЛЬКО url: `https://aab.uz/ru/press_center/tenders/all/` (полный охват, дедуп по external_id; /current/ — только активные, может быть 0). Селекторы не трогать.

## TRUSTBANK — OK
- Листинг https://trustbank.uz/ru/press_center/tenders/last/ → 200; div.tenders-list присутствует, 15 items (12 `item has-preview-text` + 3 `item`) = ровно 15 строк краулера. Селекторы валидны.
- Deep-link https://trustbank.uz/ru/press_center/tenders/last/vnimanie-tender-%3Blkjhgfdsdfghjk/ → 200, `<title>Внимание, тендер! - ЧАБ «Трастбанк»` — совпадает с title в БД.
- Негативный тест /last/bogus-xyz-123/ → 404 (честный). ✓
- Нюанс (не баг линков): банк публикует одинаковые заголовки "Внимание, тендер!" — title малоинформативен, предмет лота только в теле/вложении. Польза от detail-enrichment.
- Вердикт: DIRECT_OK, fix не нужен.

## SQB — OK (с нюансом soft-200)
- Листинг https://sqb.uz/press-center/tenders-ru/ → 200, 18 вхождений news-item = ровно 18 строк краулера. Ссылки относительные, template https://sqb.uz{link} ок.
- Deep-link .../razvitiya-sektora-teplits9552/ → 200, "теплиц" 5 вхождений (title лота на странице). ✓
- Негативный тест bogus-slug → **HTTP 200 пустая оболочка** (пустой <title>, 0 news-item) — soft-200: мёртвые ссылки не будут отдавать 404. Для краулера не критично (id берутся с живого листинга), но health-check по HTTP-коду для sqb бесполезен — проверять наличие title в HTML.
- Вердикт: DIRECT_OK, fix не нужен.

## ANORBANK — структура ОК, сайт сейчас деградирован
- Листинг с Mac: скачалось 637КБ и связь стопорится (curl timeout на 30с/60с, HTTP 000), но в partial HTML видны 4 полных item: div.news_list_wrapper_items ×4, a.news_list_wrapper_items_article ×4, _article-bottom ×4 — селекторы конфига ВАЛИДНЫ.
- href с листинга `/about/press-center/tendery/tekhnicheskoe-zadanie-na-kapitalnyy-remont-pomeshcheniy-bukharskogo-op-/` побайтно совпадает с source_url в БД → шаблон https://www.anorbank.uz{link} корректен.
- Deep-link сейчас НЕ грузится (60с timeout с Mac; с VPS 403 без прокси by design, прокси флапает: успех 14:00, фейлы 16:00/18:00). Сайт банка деградирован на момент аудита (2026-06-11 ~18:40 UTC), это runtime-нестабильность, не конфиг-баг.
- Негативный тест невозможен (сайт не отвечает).
- Вердикт: UNVERIFIABLE (confidence medium, структура и шаблон доказаны по листингу+БД; сам лот не открылся из-за даунтайма). Fix конфига не нужен; опционально — retry/backoff и второй прокси.
# Аудит deep-links — кластер BANKS (nbu, sqb, aab, anorbank, trustbank)
Дата: 2026-06-12. Аудитор: subagent corp-banks.

## 1. Конфиги (sources.yaml)
| id | name (=source в БД) | adapter | enabled | листинг | container | link | template |
|----|---------------------|---------|---------|---------|-----------|------|----------|
| nbu | НБУ (Нац. банк ВЭД) | html | true | nbu.uz/ru/tendery | a.newsfeed_01_news-item | @href | https://nbu.uz{link} |
| sqb | Узпромстройбанк (SQB) | html | true | sqb.uz/press-center/tenders-ru/ | .news-item | div.news-title a@href | https://sqb.uz{link} |
| aab | Asia Alliance Bank | html | true | aab.uz/ru/press_center/tenders/last/ | div.post | a.post__title@href | https://aab.uz{link} |
| anorbank | Anor Bank | html | true (use_proxy: 403 с VPS без прокси) | www.anorbank.uz/about/press-center/tendery/ | div.news_list_wrapper_items | a.news_list_wrapper_items_article@href | https://www.anorbank.uz{link} |
| trustbank | Трастбанк | html | true | trustbank.uz/ru/press_center/tenders/last/ | div.tenders-list div.item | h3 a@href | https://trustbank.uz{link} |

Замечание: aab.uz и trustbank.uz в конфиге имеют ОДИНАКОВЫЙ путь листинга `/ru/press_center/tenders/last/` — похоже на копипасту с trustbank, проверить существует ли такой путь на aab.uz. Есть sqb-old (disabled, заменён на sqb).

## 2. БД (tenders, snapshot 2026-06-12)
| source | rows | last_collected | sample source_url (последний) |
|--------|------|----------------|-------------------------------|
| Anor Bank | 21 | 2026-06-11 14:00 | https://www.anorbank.uz/about/press-center/tendery/tekhnicheskoe-zadanie-na-kapitalnyy-remont-pomeshcheniy-bukharskogo-op-/ |
| НБУ (Нац. банк ВЭД) | 9 | 2026-06-11 20:00 | https://nbu.uz/ru/tendery/ao-uznacbank-obieiavliaet-otbor-nailucsix-predlozenii14_05_2 (external_id=14 — числовой!) |
| Трастбанк | 15 | 2026-06-11 20:00 | https://trustbank.uz/ru/press_center/tenders/last/vnimanie-tender-%3Blkjhgfdsdfghjk/ (слаг с клавиатурным мусором — так на сайте банка) |
| Узпромстройбанк (SQB) | 29 | 2026-06-11 20:00 | https://sqb.uz/press-center/tenders-ru/akb-uzpromstroybank-obyavlyaet-o-razrabotke-dolgosrochnoy-programmy-razvitiya-sektora-teplits9552/ |
| Asia Alliance Bank | **0** | — | ПОДТВЕРЖДЕНО: 0 строк за всю историю. enabled=true. Диагностика ниже. |

Аномалии для проверки:
- НБУ: external_id=14/15 (числовой, не слаг), в title вклеена дата+хвост ("…предложений14.05.2026База RIP") — селектор title тянет вложенные div'ы. URL обрезан на ~60 символов? ("…predlozenii14_05_2") — проверить живость.
- Anor Bank: last_collected 14:00 vs 20:00 у остальных — возможно прокси-запуск нестабилен (интермиттентно).

## 3. AAB (Asia Alliance Bank) — ДИАГНОЗ 0 строк: листинг 404
- Конфиг-URL `https://aab.uz/ru/press_center/tenders/last/` → **HTTP 404** (копипаста пути trustbank: у Трастбанка `/ru/press_center/tenders/last/` существует, у AAB — нет).
- Сайт ЖИВ. Реальный листинг: **`https://aab.uz/ru/press_center/tenders/all/`** → HTTP 200, на странице 15 тендеров.
- Селекторы конфига ВАЛИДНЫ на реальной странице (проверено по HTML): `div.post` ×15, `a.post__title` (текст title + относительный href `/ru/press_center/tenders/all/<slug>/`), `div.post__date span` есть ("Дата опубликования: 05.06.2026" / "Дата истечения: 19.06.2026" — первый span = дата публикации, не дедлайн; некритично).
- Deep-link detail: `https://aab.uz/ru/press_center/tenders/all/akb-asia-alliance-bank-obyavlyaet/` → 200, title лота «...внедрение системы XDR (EDR + NDR)» в <title> и в теле. Шаблон `https://aab.uz{link}` корректен.
- Негативный тест: bogus slug → 404 (чисто, не отдаёт 200-заглушку).
- **Вердикт: SOURCE_DEAD (сбор) из-за 404 листинга; антибота нет, селекторы менять не надо.**
- **fix_proposal (проверен на живом HTML):** в sources.yaml для id=aab заменить ТОЛЬКО url: `https://aab.uz/ru/press_center/tenders/last/` → `https://aab.uz/ru/press_center/tenders/all/`. Остальное (селекторы, template) не трогать.

## 4. NBU (НБУ)
- **Сайт недоступен с аудиторской сети** (Mac): TCP connect timeout на 443 И 80 (IP 185.104.210.5) — вероятно geo/файрвол-фильтр; с VPS краулер собирает (last_collected вчера 20:00). НЕ путать с дохлым источником.
- Верификация через web.archive.org (снапшот листинга 2026-03-17): селектор `a.newsfeed_01_news-item` валиден (8 шт), href = прямые deep-links `/ru/tendery/<slug>` → шаблон `https://nbu.uz{link}` корректен. CDX: однотипные detail-URL (напр. `...predlozenii10_04`) отдавали **200**. Точный DB-URL (`...predlozenii14_05_2`) в архиве отсутствует (не заархивирован) — живость конкретной строки UNVERIFIABLE отсюда, но паттерн подтверждён.
- **БАГ title (подтверждён по HTML снапшота):** селектор `div.newsfeed_01_news-title` включает вложенные `<h5>` (заголовок) + `<p class=...-content-date>` (дата) + `<div class=...-content-paragraph>` (объект) → титулы вида «…предложений14.05.2026База RIP» (склейка). Заголовки h5 у всех лотов ОДИНАКОВЫЕ («АО Узнацбанк объявляет отбор наилучших предложений»), различитель — paragraph.
- **БАГ external_id (КРИТИЧНО, код html.py:252):** без `external_id_regex` адаптер берёт ПЕРВОЕ `(\d+)` из href → для слага `...predlozenii14_05_2` ext_id=«14» (день из даты!). Два разных лота от 14-го числа (`14_05_1`, `14_05_2`) → ОДИН ext_id `nbu-14` → дедуп молча теряет второй лот. Вероятная причина того, что в БД всего 9 строк.
- **Вердикт: DIRECT_OK** (deep-link = живой href листинга, по archive-паттерну 200), но 2 фикса:
  - **fix_proposal 1 (id):** в sources.yaml nbu добавить `html_selectors.external_id_regex: "/ru/tendery/([^/?#]+)"` → ext_id = полный слаг, коллизии исчезают (поддержка regex в html.py:245 есть).
  - **fix_proposal 2 (title, опционально):** title: `h5.newsfeed_01_news-content-heading` чистый, но одинаков у всех — рекомендую составной: оставить как есть ЛИБО title=`div.newsfeed_01_news-text-wrapper` (то же), а лучше в enrichment чистить регэкспом дату. Минимум: deadline-селектор заполнить `p.newsfeed_01_news-content-date` (сейчас пустой).

## 5. SQB (Узпромстройбанк)
- Листинг `https://sqb.uz/press-center/tenders-ru/` → 200, селекторы валидны (`.news-item` присутствует, `div.news-title a@href` → `/press-center/tenders-ru/<slug>/`).
- Sample deep-link из БД (`...teplits9552/`) → **200**, title лота в теле страницы («сектора теплиц» ×6, на листинге ×2 → это отдельная detail-страница, не листинг). H1 generic («Тендеры и конкурсы»), title в контенте.
- **Негативный тест: bogus slug → HTTP 200 (SOFT-404)** — app-shell с клиентским 404 ("404" в JS-разметке, слаг эхом). Статус-кодом битый id не ловится; валидность только по наличию title в HTML.
- external_id: тот же системный риск `(\d+)` — у sample ext_id="9552" (суффикс слага, ок), но второй sample без цифр → ext_id=полный слаг (обрезан до 100 символов). Слаг с цифрой в середине (напр. «dn-200v») дал бы ext_id="200" → коллизии возможны.
- **Вердикт: DIRECT_OK.** fix_proposal (гигиена, не срочно): `external_id_regex: "/press-center/tenders-ru/([^/?#]+)"` для стабильного id-слага.

## 6. TRUSTBANK (Трастбанк)
- Листинг `/ru/press_center/tenders/last/` → 200, селекторы валидны: `div.tenders-list div.item`, `h3 a@href` → `/ru/press_center/tenders/last/<slug>/`, caption содержит «Дата опубликования/истечения».
- Sample deep-link из БД (`vnimanie-tender-%3Blkjhgfdsdfghjk/` — слаг с клавиатурным мусором сгенерён CMS банка) → **200**, <title> «Внимание, тендер! - ЧАБ «Трастбанк»». Негативный тест: bogus → **404** (чисто).
- **Проблема качества (не deep-link):** заголовки у банка генерик («Внимание, тендер!», «Внимание, конкурс!») — `keywords_fields: [title]` по таким título НИКОГДА не сматчит товарные ключи. Реальный предмет («…лицензий DLP…») только в detail-странице, класс `div.detail-text`. Если detail enrichment (П7) не покрывает trustbank — кейворд-алерты по нему слепые.
- external_id: ext_id="3" у sample — первое `(\d+)` из «%3B» в href! Хрупко, коллизии вероятны (любой слаг с %3B даст «3»).
- **Вердикт: DIRECT_OK.** fix_proposal: `external_id_regex: "/tenders/last/([^/?#]+)"`; + включить trustbank в detail enrichment (источник тайтлов — `div.detail-text`).

## 7. ANORBANK (Anor Bank)
- **Сайт недоступен с аудиторской сети**: TCP connect timeout на 443 (IP 94.141.86.178, UZ-хостинг) — гео-фильтр, согласуется с конфигом (`use_proxy: true`, «403 from VPS without proxy»). Краулер собирает через прокси: 21 строка, last_collected 2026-06-11 **14:00** (остальные банки — 20:00; слабый сигнал интермиттентности прокси-запусков, как у agmk; не криминал — collected_at обновляется только при новых лотах).
- Верификация через Wayback (снапшот листинга 2026-03-09): селекторы валидны — `div.news_list_wrapper_items` (9 шт), `a.news_list_wrapper_items_article@href` → `/about/press-center/tendery/<slug>/`, `h3` title, `...-bottom span` = дата. Шаблон `https://www.anorbank.uz{link}` корректен.
- CDX: detail-страницы паттерна отдавали 200 вплоть до 2026-05-19 (`anorbank-obyavlyaet-tender-/`). Точный DB-URL (`...bukharskogo-op-/`) не заархивирован → живость конкретной строки UNVERIFIABLE отсюда, паттерн подтверждён.
- external_id: слаги анорбанка обычно без цифр → ext_id = полный слаг (ок). Но слаги вида `anorbank-obyavlyaet-tender-05-03-2025` дали бы ext_id="05" → тот же системный риск первого `(\d+)`.
- **Вердикт: DIRECT_OK** (по согласованности БД-URL = href листинга + archive-паттерн 200). fix_proposal: не нужен для ссылок; гигиена — `external_id_regex: "/tendery/([^/?#]+)"`.

## ИТОГ
| Источник | Вердикт | Evidence | Fix |
|----------|---------|----------|-----|
| nbu | DIRECT_OK (live-проверка с этой сети невозможна — TCP timeout 80/443, гео; верифицировано Wayback) | href листинга = deep-link `/ru/tendery/<slug>`, archive 200 на однотипных detail | НУЖЕН (не ссылки, а id/title): `external_id_regex: "/ru/tendery/([^/?#]+)"` — сейчас ext_id="14" из даты в слаге → коллизии лотов одного дня = тихая потеря (в БД всего 9 строк); deadline: `p.newsfeed_01_news-content-date` |
| sqb | DIRECT_OK | листинг 200, селекторы ок; sample deep-link 200, title лота в теле ×6; негатив = SOFT-404 (200 app-shell) | не нужен (гигиена: external_id_regex "/press-center/tenders-ru/([^/?#]+)") |
| aab | SOURCE_DEAD (сбор; сайт жив) | конфиг-листинг `/ru/press_center/tenders/last/` = **404** (копипаста пути trustbank); реальный `/ru/press_center/tenders/all/` = 200, 15 лотов, селекторы валидны без изменений; detail 200 + title; негатив 404 | **url → `https://aab.uz/ru/press_center/tenders/all/`** (одна строка, проверено на живом HTML) |
| anorbank | DIRECT_OK (live недоступен — гео-блок TCP timeout; Wayback + прокси-сбор подтверждают) | 21 строка в БД, last 11.06 14:00; селекторы подтверждены снапшотом 03.2026; CDX detail 200 по 05.2026 | не нужен для ссылок; наблюдать интермиттентность прокси (14:00 vs 20:00) |
| trustbank | DIRECT_OK | листинг 200; sample deep-link (слаг с %3B) 200, <title> совпадает; негатив 404 | не нужен для ссылок; КАЧЕСТВО: title генерик «Внимание, тендер!» → keywords слепые; включить в detail enrichment (`div.detail-text`); external_id_regex "/tenders/last/([^/?#]+)" (сейчас ext_id="3" из «%3B») |

Сквозная находка кластера: fallback `re.search(r"(\d+)", link)` в html.py:252 для external_id хрупок на слагах с датами/моделями — рекомендован per-source `external_id_regex` (поддержка в коде есть, html.py:245). Алерты: у всех sample-строк alert_seq=null (банковские лоты не матчат полиграфические кейворды — ожидаемо, не баг ссылок).
