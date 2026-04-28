# Фаза C: Международные организации — Результаты исследования

> Дата: 3 марта 2026 | Исследовано: 60+ организаций, 8 параллельных агентов

---

## Стратегия: 4 API = 80% покрытия

| # | Источник | API URL | Auth | Тендеров UZ | Покрывает |
|---|----------|---------|------|-------------|-----------|
| 1 | **UNGM** | `developer.ungm.org` | API key (рег.) | **50-100+** | 40+ агентств ООН (UNOPS, UNICEF, WHO, WFP, FAO, IOM, UNFPA, ILO, UNESCO, UNIDO, UNHCR, IFAD) |
| 2 | **TED (EU)** | `api.ted.europa.eu/v3/notices/search` | Анонимный | 5-20 | Все EU procurement (EuropeAid, DG INTPA, NDICI) |
| 3 | **Grants.gov** | `api.grants.gov/v1/api/search2` | **Без ключа!** | 5-15 | USAID гранты + cooperative agreements |
| 4 | **SAM.gov** | `api.sam.gov/prod/opportunities/v2/search` | Бесплатный API key | 5-15 | USAID контракты + все US federal |
| — | **World Bank** | `search.worldbank.org/api/v2/procnotices` | Без ключа | 20-50 | **Уже подключён** |

**Итого через API: ~100-200 тендеров** в любой момент времени.

---

## HTML Scraping (Tier 2) — добавить после API

| # | Источник | URL | Адаптер | Тендеров UZ | Приоритет |
|---|----------|-----|---------|-------------|-----------|
| 5 | **ADB** | `adb.org/projects/tenders/country/uzb` | html/spa | 30-60+ | **Высокий** |
| 6 | **EBRD** | `ecepp.ebrd.com` (Delta eSourcing) | spa | 10-30 | **Высокий** (нужна рег.) |
| 7 | **IsDB** | `isdb.org/project-procurement/tenders` | html | 10-20 | **Высокий** |
| 8 | **AIIB** | `aiib.org/en/opportunities/business/project-procurement/list.html` | html | 5-15 | Высокий |
| 9 | **EDB/ЕАБР** | `eabr.org/en/procurement/` | html | 3-10 | Высокий (новый член) |
| 10 | **OSCE** | `procurement.osce.org/tenders?f[]=source:OSCE+Project+Co-ordinator+in+Uzbekistan` | html | 3-8 | Средний |
| 11 | **JICA** | `jica.go.jp/english/.../tender/` | html | 3-8 | Средний |
| 12 | **GIZ** | `ausschreibungen.giz.de` (Cosinex VMP) | spa | 3-10 | Средний |
| 13 | **Aga Khan** | `akf.org/tenders/` | html | 2-5 | Средний |
| 14 | **EIB** | `eib.org/en/about/procurement/all/index` | html | 2-5 | Низкий |
| 15 | **UNODC ROCA** | `unodc.org/roca/en/About-unodc-roca/procurement-notices.html` | html | 3-8 | Средний (полиграфия!) |

---

## Обратные тендеры — пропущенные площадки

| Площадка | URL | Что даёт | Приоритет |
|----------|-----|----------|-----------|
| **exarid.uzex.uz** | `exarid.uzex.uz/ru/tender2` | Корпоративные закупки госкомпаний | **Критический** |
| **dxarid.uzex.uz** | `dxarid.uzex.uz/ru/tender2/` | Госзакупки бюджетных организаций | **Высокий** |
| **zakupki.prom.uz** | `zakupki.prom.uz` | B2B корпоративные (19k+ поставщиков) | Средний |
| **d-xarid.uz** | `d-xarid.uz` | Прямые закупки малого бизнеса | Средний |

### UZEX API discovery (проверить с Mac)
```bash
# По аналогии с apietender.uzex.uz — проверить:
curl -X POST https://apiexarid.uzex.uz/api/Common/TradeList -H "Content-Type: application/json" -d '{"from":0,"to":10}'
curl -X POST https://apidxarid.uzex.uz/api/Common/TradeList -H "Content-Type: application/json" -d '{"from":0,"to":10}'
```

### cooperation.uz — проверить доп. endpoints
```bash
curl https://cabinet.cooperation.uz/api/tender/public/lots
curl https://cabinet.cooperation.uz/api/competition/public/lots
curl https://cabinet.cooperation.uz/swagger/index.html
curl https://new.cooperation.uz/ocelot/api-client/Client/GetAllCompetition
```

---

## Lead Generation: Закупочные планы

**cooperation.uz GetAllPlanSchedule (375k записей)** — уже парсим, но не используем для lead gen.

**Стратегия:**
1. Фильтровать планы по нашим ключевым словам (упаковка, печать, полиграфия)
2. Отдельный тип алерта «ПЛАН ЗАКУПКИ» в Telegram
3. Проактивное КП: связаться с заказчиком ДО объявления тендера

---

## Обход регистрации — способы получить данные без E-IMZO

### Что работает
1. **Google/Yandex cache** — `site:ebirja.uz тендер`, `site:hayotbirja.uz` — поисковики индексируют страницы лотов
2. **Telegram-каналы площадок** — некоторые площадки дублируют в TG
3. **Агрегаторы как прокси** — TenderZone (7 дней бесплатно), Bicotender
4. **Sitemap.xml** — проверить на каждой площадке

### Что НЕ работает / не нужно
- e-auksion.uz, online-mulk.uz, online-auksion.uz — площадки ПРОДАЖИ (не обратные аукционы)
- eshop.uzex.uz, milliydokon.uzex.uz — устарели, перенесены в xarid.uzex.uz с 01.01.2022
- Боты @newtenderzone_bot, @bicotender_bot — платные, данные из тех же площадок

---

## Организации БЕЗ тендеров (не парсить)

| Организация | Причина |
|-------------|---------|
| SCO | Политическая организация, нет procurement |
| ECO | Нет procurement раздела |
| Turkic Council | Нет procurement |
| TRACECA | Тендеры через MDBs |
| EFSD | Узбекистан НЕ член |
| CIF | Тендеры через MDBs |
| GAVI | Через UNICEF/UNGM |
| GEF | Через UNDP/WB/ADB (71 проект в UZ) |
| TIKA | Прямые контракты, нет публичных тендеров |
| Open Society | Гранты по приглашению |
| ReliefWeb | Отчёты, не тендеры |

---

## План подключения (приоритизированный)

### Неделя 1 — API (максимальный ROI)
1. [ ] **Grants.gov** — без auth, подключить сразу (POST api)
2. [ ] **TED EU** — анонимный API, подключить сразу
3. [ ] **UNGM** — зарегистрироваться на developer.ungm.org, получить API key
4. [ ] **SAM.gov** — зарегистрироваться, запросить API key (может до 4 недель)

### Неделя 2 — UZEX discovery + HTML scraping
5. [ ] **exarid/dxarid API** — проверить endpoints через DevTools/curl
6. [ ] **ADB** — HTML scraping `/tenders/country/uzb`
7. [ ] **IsDB** — HTML scraping
8. [ ] **OSCE** — HTML Drupal scraping
9. [ ] **EDB/ЕАБР** — HTML scraping

### Неделя 3 — Расширение
10. [ ] **EBRD** — SPA Playwright (нужна регистрация)
11. [ ] **AIIB** — HTML + PDF
12. [ ] **JICA, GIZ** — HTML scraping
13. [ ] **Aga Khan, EIB** — HTML scraping
14. [ ] **zakupki.prom.uz** — HTML scraping

### Lead Generation
15. [ ] **GetAllPlanSchedule** — фильтр по ключевым словам, отдельный алерт

---

## Детали API: Grants.gov (подключить первым — без auth!)

```yaml
- id: grants-usaid
  name: "Grants.gov USAID Uzbekistan"
  adapter: api
  enabled: true
  url: "https://api.grants.gov/v1/api/search2"
  method: POST
  headers:
    Content-Type: "application/json"
  body:
    keyword: "Uzbekistan"
    agencies: "USAID"
    oppStatuses: "posted|forecasted"
    rows: 100
    offset: 0
  rate_limit: 2
  timeout: 30
  id_prefix: "grants"
  response_path: "oppHits"
  field_map:
    title: title
    organization: agencyName
    deadline: closeDate
    date_start: openDate
    external_id: oppNumber
    source_url_template: "https://www.grants.gov/search-results-detail/{id}"
  keywords_fields: [title, agencyName, description]
  pagination:
    type: offset
    param: "offset"
    size_param: "rows"
    page_size: 100
    max_pages: 5
```

## Детали API: TED EU

```yaml
- id: ted-eu
  name: "TED EU Uzbekistan"
  adapter: api
  enabled: true
  url: "https://api.ted.europa.eu/v3/notices/search"
  method: GET
  params:
    q: "Uzbekistan"
    limit: 100
    page: 0
  rate_limit: 1
  timeout: 30
  id_prefix: "ted"
  response_path: "notices"
  country_filter: "uzbekistan"
  field_map:
    title: title
    organization: buyerName
    deadline: submissionDeadline
    date_start: publicationDate
    external_id: noticeId
    source_url_template: "https://ted.europa.eu/en/notice/-/detail/{id}"
  keywords_fields: [title, buyerName, description]
  pagination:
    type: page
    param: "page"
    size_param: "limit"
    page_size: 100
    max_pages: 5
```

## Детали API: UNGM (нужен API key)

Developer Center: https://developer.ungm.org/
- `GET /Article/GetNotices` — получение тендеров
- `POST /Article/SearchNotices` — поиск с фильтрами
- `GET /Article/NoticeHelpers` — справочники (страны, коды)

## Детали API: SAM.gov (нужен бесплатный API key)

```
GET https://api.sam.gov/prod/opportunities/v2/search
  ?api_key=KEY
  &postedFrom=MM/DD/YYYY
  &limit=1000
  &title=Uzbekistan
```

Поля: title, fullParentPathName, responseDeadLine, postedDate, noticeId, uiLink, placeOfPerformance.country.code=UZB
