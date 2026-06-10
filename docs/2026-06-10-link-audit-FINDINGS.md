# Аудит ссылок + охват — сессия 2026-06-10 (частичный, прерван лимитами)

Контекст: Данияр показал 2 объявления Winch в э-магазине xt-xarid (7628192 «Цвет белый», 7627284 «Цвет синий» — Блокнот А5, полное печатное ТЗ), которых **вообще не было в БД краулера**. Задача: чинить ссылки в алертах + найти всё, что не видим.

## ✅ СДЕЛАНО И ЗАДЕПЛОЕНО (verified в проде)

### PR #8 (merged): новый источник `xt-xarid-shop`
- **Корень 1:** `hayotbirja-shop` был enabled, но за всю историю собрал **0 строк** — `field_map title: "name"`, а поле `name` у объявлений э-магазина ПУСТОЕ (имя в `product_name`) → адаптер дропал каждую строку (`len(title)<3`). Freshness-watchdog слеп к источникам, у которых никогда не было строк.
- **Корень 2:** xt-xarid shop-источника не существовало. hayotbirja и xt-xarid — **один бэкенд** (одни и те же id объявлений, проверено curl на обоих API) → нужен только ОДИН источник.
- **Фиксы:** jsonrpc-адаптер: title pipe-fallback (`product_name|mark|name`) + `_price_times_amount` (API отдаёт только цену/ед → без умножения на количество MIN_PRICE 10M глушил бы всё). Источник: `ref_online_shop_public`, сортировка ответа = `close_at DESC` ≈ новейшие первыми, offset cap 3000 (≈16ч потока, ~4500 объявл./день) — 30 страниц × 100 при 3 краулах/день покрывают поток. Deep-link `https://xt-xarid.uz/procedure/{id}/core` открывается анонимно (проверено рендером), имя "XT-Xarid э-магазин" не матчится broken-spa префиксом.
- **Прод-верификация:** VPS подхватил за ~10 мин, краул 06:02 UTC: **2970 строк, 23 алерта (#2530-2552)** — Буклет/Брошюра (конкурент Print makon!), Печатная продукция (AJ Sharq), Книга Регистрации, Амбулаторная карта, Бланки, Таблички, гофрокороба, бумажные пакеты.
- Объявления Winch (7628192/7627284) старше окна свежести → в БД не попали (не баг; новые такие будут ловиться). Бэкфилл этих двух — опционально через VPS.

### PR #9 (merged): precision-фикс relevance для э-магазина
Первый краул дал FP: **Epinephrine** (лекарство) score 95 («печать этикеток для ампул»), Лента скоростемерная, Новогодний подарок. Корень: промпт рамкирует всё как запрос покупателя → AI фантазирует заказ на печать за объявлением поставщика; «ИД упаковки» в фарм-тексте триггерит «упаковку». Фикс: условный блок `{source_context}` только для источников `*э-магазин*` — оценивать сам ТОВАР (полиграфия/бумага-картон/стенды/таблички/бейджи); лекарства/техника/еда = irrelevant. **Эффект проверить на следующем краула** (алерты после ~14:00 UTC 10.06).

## 📊 Workflow-аудит (52 агента отработало, ~30 упало на лимитах)

Полный JSON: `2026-06-10-link-audit-workflow-partial.json` (75KB). Скрипт: `2026-06-10-link-audit-workflow-script.js`.
Resume: runId `wf_68f0d216-372` (same-session only; в новой сессии — переиспользовать скрипт, готовые результаты из JSON).

### 🔴 Подтверждённые СЛОМАННЫЕ ссылки (фиксы готовы, НЕ внедрены)
| Источник | Проблема | Фикс |
|---|---|---|
| **B2Biz.uz Тендеры** | шаблон `/home/tenders/{id}` → SPA 404 (роута нет в бандле) | → `https://b2biz.uz/home/tender/{id}/overview` (singular!); аноним → /login (auth-wall площадки); healthcheck: анонимный API `GET https://b2biz.uz/api/v1/etp/get-list-published-procedures/` (guid/ptitle/pstatus); в конфиге `broken_spa: true` |
| **B2Biz.uz Планы** | статический листинг без {id} (165 записей на 1 URL) | → `https://b2biz.uz/home/procurement-plans/{id}/row` (нативный share-URL SPA); API `GET /api/v1/etp/plan/proc/list/` анонимный |
| **minstroy-tenders (tender.mc.uz)** | `/tender/{id}` → клиентский redirect на главную (роута нет) | → `https://tender.mc.uz/tender-list/tender/{id}/view`; {id} = ЧИСЛОВОЙ id (257680), не unique_name; healthcheck: `GET https://apisitender.mc.uz/api/tenders/{id}`; `broken_spa: true` |
| **tender-mc (дубль)** | то же + молчит с 2026-04-15 | тот же шаблон; кандидат на `enabled: false` (дубль minstroy-tenders) |
| **snap.py рассинхрон** | коммент «UZEX Предкв stays» НЕ отражён в коде — "UZEX Предквалификации" ОТСУТСТВУЕТ в BROKEN_SPA_SOURCES | проверить нужность: с 08.06 шаблон proposal-request/detail верный и публичный → возможно всё ок, но сверить |
| **xt-xarid prefix case** | `BROKEN_SPA_PREFIXES` содержит `"xt-xarid"` (lowercase), источники называются "XT-Xarid …" → префикс НЕ матчится. Для встречных/тендеров это СЕЙЧАС правильно (ссылки публичны), но логика хрупкая | нормализовать `source.lower().startswith()` + явный allowlist |

### ✅ Подтверждённые РАБОЧИЕ ссылки (фикс не нужен)
etender.uzex.uz `/lot/{id}` (2/2 сэмпла через `apietender.uzex.uz/api/common/GetTrade/{id}/0`), worldbank procurement-detail (2/2 через search.worldbank.org API), **все 19 Telegram-источников** (embed-проверка t.me, точные совпадения текстов), xt-xarid procedure/{id}/core (reduction+tender+ad через urpc get_proc), tashkentsteel `/lot/{id}` (SSR, честный 404), ipotekabank (+совет: www-host против 301), hamkorbank (РИСК: href без ведущего слэша — нормализовать urljoin), uzairways (Drupal SSR), undp.org (SSR + data-noticeid из POST Search).

### ⚠️ UNVERIFIABLE
cooperation.uz — **geo-block на TCP-уровне для не-UZ IP** (Mac и US-прокси = connect timeout). Шаблон `/plan-schedule/{uuid}` корректен по history (апрель 2026, проверено из UZ). Верификацию гонять с VPS через UZ-proxy.

### 💀 Мёртвые enabled-источники (0 строк или молчат >30д) — 15 шт
`UNDP Procurement`, `UN Global Marketplace`, `TG: Закупки Prom.uz` (канал жив, но 0 строк — проверить подписку аккаунта!), `TG: Фонд предпринимательства` (канал почти не постит — возможно норма), `Asia Alliance Bank`, `GIZ Ausschreibungen`, `E-Birja встречные` (API пуст — известно), `E-Birja торговые/аукционные (авториз.)`, `OSCE` (23.04), `Ипотека-банк` (05.05), `Хамкорбанк` (07.05), `Минстрой tender.mc` (15.04, рассинхрон yaml/KNOWN_RETIRED), `E-Birja активные аукционы` (15.04, тот же рассинхрон), `Hayotbirja э-магазин` (починен через xt-xarid-shop, PR #8).
**Фикс watchdog:** freshness_watchdog сравнивает только источники, ПРИСУТСТВУЮЩИЕ в БД — добавить сверку enabled-в-yaml vs есть-в-БД.

### Логика ссылок в notifier (карта, из Map-агента)
adapter → `tender.source_url` (api/jsonrpc: шаблон; html: href/urljoin, при пустом link-селекторе source_url=""; spa: шаблон ИГНОРИРУЕТСЯ, берётся href; telegram: t.me/{username}/{msg_id}). notifier `_format_alert`: broken_spa → только Vercel detail + скриншот; иначе прямая ссылка + Vercel-архив; Cooperation.uz → доп. SEARCH_FALLBACK supplier/all?productName; broken-SPA → блок «Подача КП» (корень площадки + номер лота).

## ❌ НЕ сделано (упало на лимитах — повторить после reset 19:20)
1. **Platform deep-probes** (все 6): xt-xarid/hayotbirja (остальные `ref_*_public`: selection/request_proposals/master_agreement + каталог print product_ids), new-xarid UZEX (`api/Public/*`: результаты торгов, победители), cooperation (gateway/api-stat/*), e-birja, etender+xarid.uzex (почему 5669+4717 строк дают 0-2 алерта — вероятно позиции лота не в search_text!)
2. **Research**: агрегаторы/TG/openbudget + **флоу подачи КП** (deep-link до bid-формы, API мобильных приложений)
3. **Верификация** 4 broken-link claims (b2biz×2, tender.mc×2) — evidence сильный и без верификации
4. **Аудит корп. сайтов** (~16: agmk, railway, beeline, nbu, sqb, banks…)
5. **Внедрение фиксов ссылок** из таблицы выше (b2biz, tender.mc, hamkorbank href, watchdog enabled-vs-БД, snap.py case)

## Следующая сессия — план
1. Проверить precision э-магазина после краулов с PR #9 (FP типа Epinephrine должны уйти): `SELECT title, relevance_score, relevance_reason FROM tenders WHERE source='XT-Xarid э-магазин' AND alert_seq IS NOT NULL AND collected_at > '2026-06-10 12:00+00'`
2. Внедрить фиксы ссылок (таблица выше) — b2biz + tender.mc + hamkorbank + watchdog + snap.py
3. Дозапустить упавшие probe/research агенты (скрипт сохранён, готовые результаты в JSON — не повторять)
4. Бэкфилл 2 объявлений Winch (опционально, через VPS)
