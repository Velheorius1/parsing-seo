# Cooperation.uz coverage + Data quality framework — Design Document

> Дата: 2026-04-28
> Источник анализа: /deep-think (6 параллельных экспертных агентов)

## Table of Contents

1. [Overview](#overview)
2. [Key Decisions](#key-decisions)
3. [Detailed Analysis](#detailed-analysis)
   - [1. Cooperation API surface](#1-cooperation-api-surface)
   - [2. productName filter refactor](#2-productname-filter-refactor)
   - [3. Dead Cooperation endpoints](#3-dead-cooperation-endpoints)
   - [4. Data quality scoring framework](#4-data-quality-scoring-framework)
   - [5. Structured logging](#5-structured-logging)
   - [6. Dead non-Cooperation sources](#6-dead-non-cooperation-sources)
4. [Implementation Plan](#implementation-plan)
5. [Success Metrics](#success-metrics)

## Overview

**Проблема.** Cooperation.uz — крупнейший портал госзакупок Узбекистана, но healthcheck показывает 4 dead Cooperation source за 7 дней (Закупочные планы / Аукционы / Э-магазин лоты / Брошюры). Из 10 productName-источников каждый возвращает 22-1050 записей — это <2% от реального объёма площадки (86K+ schedule plans). Параллельно 12 не-Cooperation источников тоже dead (включая XT-Xarid тендеры — главная госплощадка!).

**Что обнаружили.** Архитектура разорвана пополам:
- **Path A:** `crawler/config/sources.yaml` (10 yaml-блоков по ~40 строк, 393 строки дублирования) — даёт 1180 records.
- **Path B:** `scripts/fetch_cooperation.py` (5 standalone функций, full-feed без filter) — даёт 62K records (Лоты 34K + Оферты 24K).
- Cron `scripts/run_proxy_fetch.sh` запускает только Path B `lots`+`offers`. Поддерживаемые `--source plans/auction/eshop` **не вызываются с 28 марта** — это и есть причина 3 dead sources.
- `cooperation-broshyura` падает из-за изменения схемы upstream: `item_filter: {unit: {gt: 0}}` отбрасывает 100% записей (теперь cooperation отдаёт `unit: 0.00` для черновиков).
- `XT-Xarid тендеры` тих не из-за API, а из-за **cross-source dedup**: `dedup_group: "birja-tender"` совпадает с hayotbirja-tender → второй источник всегда теряет.

**Цель решения.** (1) Восстановить покрытие Cooperation × 5-10. (2) Убрать silent failures через per-source quality + structured logging. (3) Починить XT-Xarid (главная госплощадка).

## Key Decisions

| # | Aspect | Decision | Confidence |
|---|--------|----------|------------|
| 1 | Cooperation API endpoints | **B+D**: full-feed для lots/offers (без filter) + E-IMZO enrichment лотов в Phase 2 | High |
| 2 | productName refactor | **B**: 1 source + `productName_values: [30 stems]` loop в адаптере (вместо 10 yaml-блоков) | High |
| 3 | Dead Cooperation endpoints | **B+C**: исправить cron (запустить plans/auction/eshop), убрать `unit > 0` из item_filter, добавить per-source SLO | High |
| 4 | Quality scoring | **B**: новая таблица `source_quality_metrics` (long-format) + `source_quality_daily` rollup + 90d retention | High |
| 5 | Structured logging | **C**: расширить `crawl_logger.py` + новая `crawl_run_sources` со `stages[]` JSONB + `run_id` correlation | High |
| 6 | Dead non-Cooperation | **C+D**: критичные починить (XT-Xarid dedup, Минстрой), мёртвые TG/банки auto-disable после 7 дней молчания | High |

---

## Detailed Analysis

### 1. Cooperation API surface
**Эксперт:** Theo Browne
**Decision:** Использовать существующий full-feed подход (Path B) для lots/offers/plans/auction/eshop. Phase 2 — E-IMZO enrichment для 34K Лотов без organization.

**Reasoning:**
- Path B уже доказал концепцию: GetAllOffer без productName-фильтра → 25K records, GetLotsInTrade → 34K records.
- Hidden endpoints за E-IMZO (`GetLotInfo` для enrichment organization, `purchase-orders`, `procurement-plans`) разблокируются только после регистрации на cooperation.uz.
- Path A's `productName` filter — отдельная задача (см. Aspect 2): для `schedule-plans/for-client` есть server-side индексированный фильтр, его выкидывать нельзя — это бесплатный indexed lookup.

**Альтернативы:**
- Реверс-инжиниринг через DevTools UI (Option D) — заблокирован E-IMZO, фрагильно. Откладывается.
- Hybrid с full-feed раз в день — overhead двух систем не оправдан.

**Risks:** rate-limit на residential proxy (170 страниц/cycle для full-feed plans); mid-crawl pagination gaps (mitigation: дедуп upsert по external_id).

---

### 2. productName filter refactor
**Эксперт:** Raymond Hettinger
**Decision:** Заменить 10 yaml-блоков (393 строки) одним источником с полем `productName_values: List[str]` в SourceConfig. Адаптер итерирует и dedup-ит результаты. Расширить keyword-список до ~30 stems.

**Reasoning:**
- Server-side `productName` фильтр — это БЕСПЛАТНЫЙ indexed lookup (case-insensitive substring). Выкидывать его на full-feed = отдавать назад работу, которую API сделал.
- 10 yaml-блоков с 90% дубликатами — DRY violation, drift-prone (уже есть drift: `cooperation-print` имеет лишний `deadline: ""`).
- Resolution с Aspect 1: `schedule-plans` имеет productName → loop. `Lots/Offers` НЕ имеют productName → full-feed (как Path B сейчас).

**Implementation:**
```yaml
- id: cooperation-plans
  name: "Cooperation.uz Закупочные планы"
  url: "https://cabinet.cooperation.uz/api/schedule-plan/schedule-plans/for-client"
  productName_param: "productName"
  productName_values:
    - полиграф, этикетка, печать, пакет, блокнот, конверт, стикер
    - брошюр, календарь, буклет, листовк, флаер, гофра, коробка
    - картон, наклейк, ярлик, ежедневник, визитк, футболк, сувенир
    - подарочн, кружк, бейдж, папк, qadoq, quti, chop, korobka
    - katalog, bosma
```

**Code change:** в `crawler/adapters/api.py` — если `cfg.productName_param` задан, цикл по `productName_values`, dedup по `external_id` (first occurrence wins), один объединённый список.

**Risks:** request budget 30 keywords × ~3 страницы × 0.5 rps = ~3 минуты на cycle (vs текущие 1.5 мин для 10). Mitigation: `max_pages: 5`, early-exit на page 1 если 0 результатов.

---

### 3. Dead Cooperation endpoints
**Эксперт:** Sam Newman
**Decision:** Срочный fix orchestration drift + softer item_filter + per-source freshness SLO в healthcheck.

**Per-source diagnosis:**
| Source | Last record | Fault mode | Root cause |
|---|---|---|---|
| Закупочные планы | 28.03 | Не вызывается из cron | `run_proxy_fetch.sh` без `--source plans` |
| Аукционы | 15.03 | Не вызывается из cron | `run_proxy_fetch.sh` без `--source auction` |
| Э-магазин лоты | 15.03 | Не вызывается из cron | `run_proxy_fetch.sh` без `--source eshop` |
| Брошюры/Буклеты | 17.04 | Schema change → silent filter drop | Cooperation отдаёт `unit: 0.00`, item_filter дропает 100% |

**Implementation:**
1. `scripts/run_proxy_fetch.sh` — добавить 3 строки (`--source plans/auction/eshop`).
2. `crawler/config/sources.yaml` — у всех 10 cooperation-* убрать `unit: {gt: 0}`, оставить `products[*].quantity > 0`.
3. `crawler/scripts/healthcheck.py` — новая функция `check_dead_sources()`: читает per-source last collected_at, FAIL если ≥7 дней.

**Risks:** `GetAllPlanSchedule` не тестился через cron давно (28.03) — возможно прокси требует другие headers; ослабление item_filter добавит "черновики" (unit:0) — мусор в БД, но не в Telegram-алертах (keywords отфильтруют).

---

### 4. Data quality scoring framework
**Эксперт:** Markus Winand
**Decision:** Long-format таблица `source_quality_metrics` (~6 строк × 88 sources × 12 runs/day = 6336/день, 2.3M/год) + daily rollup в `source_quality_daily` + retention 90d.

**Schema:**
```sql
CREATE TABLE source_quality_metrics (
  id BIGSERIAL PRIMARY KEY,
  source TEXT NOT NULL,
  metric_type TEXT NOT NULL,  -- 'org_pct', 'price_pct', 'deadline_pct', 'quality_score',
                              -- 'items_fetched', 'duplicate_ratio', 'avg_age_hours',
                              -- 'errors_count', 'alert_client_rate'
  metric_value NUMERIC NOT NULL,
  sample_size INT,
  run_id UUID REFERENCES crawl_runs(id) ON DELETE CASCADE,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  metadata JSONB DEFAULT '{}'
);

-- Index 1: trend per source (Q: "как менялся quality lex.uz за месяц")
CREATE INDEX idx_sqm_source_time
  ON source_quality_metrics (source, metric_type, computed_at DESC);

-- Index 2: top-N worst (Q: "5 худших sources сегодня")
CREATE INDEX idx_sqm_metric_time
  ON source_quality_metrics (metric_type, computed_at DESC)
  INCLUDE (source, metric_value);

-- Index 3: zero-fetch SLA breach (partial — компактный)
CREATE INDEX idx_sqm_zero_fetch
  ON source_quality_metrics (computed_at DESC, source)
  WHERE metric_type = 'items_fetched' AND metric_value = 0;
```

**Reasoning:**
- Уже есть `crawler/core/quality_tracker.py` (QualitySnapshot, weighted score 30/30/20/10/10), но хранит в JSONL — нельзя джойнить, нельзя SQL-аналитику.
- Long-format > wide-format: новые метрики = новый `metric_type`, без ALTER TABLE.
- Cardinality 2.3M/год — НЕ повод партиционировать (Winand: premature). Достаточно retention.

**Update flow:**
- После каждого crawl run: batch INSERT 528 rows (88 sources × 6 metrics).
- Daily cron 03:00 UTC: aggregate в `source_quality_daily`, DELETE из raw где `computed_at < now() - 90d`.
- Telegram-дайджест 09:00: top-5 худших + список zero-fetch + sources где quality упал >10pp.

**Risks:** retention forgot → 50M строк через 2 года (mitigation: cron + weekly size check); alert-conversion требует feedback в БД (сейчас JSONL — отдельная миграция).

---

### 5. Structured logging
**Эксперт:** Kelsey Hightower
**Decision:** Расширить `crawl_logger.py` + новая таблица `crawl_run_sources` с `stages[]` JSONB. `run_id` UUID связывает run-level + source-level + plain-text logs.

**Per-source row schema:**
```json
{
  "run_id": "uuid-v7",
  "source_id": "xt-xarid-tender",
  "started_at": "2026-04-27T14:00:02Z",
  "duration_ms": 61865,
  "http": {"status_codes": {"200": 12}, "retries": 1, "bytes_received": 4521000},
  "stages": [
    {"name": "fetch",            "input": null, "output": 5200, "dropped": 0,   "reason": null},
    {"name": "item_filter",      "input": 5200, "output": 4850, "dropped": 350, "reason": "qty=0"},
    {"name": "status_whitelist", "input": 4850, "output": 4520, "dropped": 330, "reason": "status=cancelled"},
    {"name": "dedup_within",     "input": 4520, "output": 4518, "dropped": 2,   "reason": null},
    {"name": "dedup_cross",      "input": 4518, "output": 9,    "dropped": 4509, "reason": "birja-tender"},
    {"name": "upsert",           "input": 9,    "output": 9,    "dropped": 0,   "reason": null}
  ],
  "errors": [],
  "skipped_no_auth": false
}
```

**Reasoning:**
- Сейчас `fetched=9, stored=0` для XT-Xarid невозможно отличить от "API вернул 0" — дроп пишется в plain text logger.
- 80% инфры уже есть (`crawl_logger.py`, `crawl_runs` table). Diff ≈ 200 строк кода + 1 миграция.
- Cardinality 30 sources × 7 stages × 3 runs/day = 630 events/day = 33K/год.

**Anomaly detection** — отдельный SQL-скрипт после `finalize()`:
```sql
WITH baseline AS (
  SELECT source_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY (stages->-1->>'output')::int) AS p50_kept
  FROM crawl_run_sources WHERE started_at > now() - interval '14 days' GROUP BY source_id
)
SELECT source_id, (stages->-1->>'output')::int AS kept, p50_kept
FROM crawl_run_sources crs JOIN baseline b USING (source_id)
WHERE run_id = $1 AND p50_kept > 10 AND (stages->-1->>'output')::int < p50_kept * 0.3;
```
→ запись в `crawl_runs.anomalies`, отправка в Telegram-дайджест.

**Risks:** schema drift JSONL ↔ Postgres (mitigation: `schema_version: 2` в JSONL); first 14 days baseline шумный (mitigation: skip anomaly если `run_count < 14`).

---

### 6. Dead non-Cooperation sources
**Эксперт:** Martin Fowler
**Decision:** Категоризировать (C) + auto-disable после 7d молчания (D). Критичные чинить сейчас, банки удалить из dashboard, low-signal TG отключить.

**Diagnosis:**
| Source | Type | 7d count | Root cause | Action |
|---|---|---:|---|---|
| **XT-Xarid тендеры** | jsonrpc | 0 | `dedup_group: "birja-tender"` совпадает с hayotbirja → cross-source dedup гасит | Разделить dedup_groups: `xt-xarid-tender` vs `hayotbirja-tender` |
| **XT-Xarid встречные аукционы** | jsonrpc | 0 | То же — `birja-reduction` совпадает | Разделить dedup_groups |
| **E-Birja аукционы (xarid)** | api | 0 | `data: []`, нужна e-imzo Bearer JWT | Добавить `Authorization` через session_store |
| **Минстрой (tender.mc.uz)** | api | 0 | API вернул пусто без `status=2` | Проверить актуальный enum, disable дубликат `tender-mc` |
| **Узбекистон Темир Йуллари** | html | **25** | **Жив**, в брифе ошибочно | Исключить из списка dead |
| TG: PR UZB | telegram | 0 | Канал не постит тендеры (1 за всё время) | `enabled: false` |
| TG: UZEX Xarid Off | telegram | 0 | Канал постит, не проходит keyword filter | `tg_inspect` debug, потом decision |
| TG: tenderweekcom | telegram | 0 | Аналогично — 3 за всё время | `enabled: false` |
| TG: Мин ЖКХ | telegram | 0 | Не постит закупки (3 за всё время) | `enabled: false` |
| InFinBank, Orient, Sanoat | — | 0 | Zombie records без yaml | SQL `is_archived = true` (soft-delete) |

**XT-Xarid — критичный.** Это главная госплощадка, dedup-регрессия её гасит. Файлы: `crawler/config/sources.yaml:2709, 2736`.

**Auto-disable (D):** в `metrics_tracker.py` правило: `if stored_7d == 0 AND fetched_7d == 0 → enabled = false` + Telegram alert. НЕ disable если `fetched > 0 AND stored = 0` (это симптом dedup, не death).

**Risks:** XT-Xarid и Hayotbirja могут быть на самом деле одним feed → split dedup создаст дубли. Mitigation: 5-минутный SQL-проверочный анализ overlap external_id перед split. Auto-disable должен иметь whitelist для критичных source.

---

## Implementation Plan

### Phase 1: Critical fixes (≤2 часа, делать сейчас)
- [ ] **Cron orchestration:** добавить в `scripts/run_proxy_fetch.sh` строки `--source plans`, `--source auction`, `--source eshop`. Запустить вручную и проверить вывод.
- [ ] **XT-Xarid dedup:** разделить `dedup_group` для `xt-xarid-tender` и `xt-xarid-reduction` (после SQL-проверки overlap с hayotbirja).
- [ ] **Item filter softer:** убрать `unit: {gt: 0}` из всех 10 cooperation-* источников в yaml.
- [ ] **Dead TG disable:** `enabled: false` для PR UZB, tenderweekcom, Мин ЖКХ.
- [ ] **Healthcheck per-source SLO:** добавить `check_dead_sources()` — FAIL если source 7d молчит (с whitelist для known-low-volume).

### Phase 2: productName refactor (≤4 часа)
- [ ] Расширить `SourceConfig`: `productName_param: Optional[str]`, `productName_values: List[str]`.
- [ ] В `adapters/api.py`: цикл по values с dedup по external_id, structured log per-keyword.
- [ ] Удалить 9 yaml-блоков (`cooperation-print/etiketka/pechat/paket/bloknot/konvert/stiker/broshyura/kalendar`), переделать в один `cooperation-plans` с 30 stems.
- [ ] Запустить dry-run, сверить с cumulative count старых 10.

### Phase 3: Quality framework (≤6 часов)
- [ ] Migration `016_source_quality_metrics.sql` (DDL выше).
- [ ] `quality_tracker.py.flush_to_supabase(run_id, snapshot)` — вызвать после `crawl_logger.finalize`.
- [ ] `scripts/quality_rollup.py` (cron 03:00 UTC) — agg в `_daily` + DELETE old.
- [ ] `scripts/quality_digest.py` (cron 09:00 UTC) — Telegram top-5 worst + zero-fetch list.
- [ ] `scripts/backfill_quality.py` — одноразовый импорт из `logs/quality_history.jsonl`.

### Phase 4: Structured logging (≤4 часа)
- [ ] Migration `017_crawl_run_sources.sql` — таблица + RLS + индексы.
- [ ] `crawl_logger.py.SourceStats.stages: List[StageEvent]` + `record_stage()`, `record_http()`, `run_id = uuid7()`.
- [ ] `adapters/api.py:295-317` — заменить `logger.info` на `crawl_log.record_stage`.
- [ ] `core/runner.py` — `record_stage` для cross-source dedup.
- [ ] `scripts/detect_anomalies.py` — SQL baseline + Telegram alert.

### Phase 5: E-IMZO enrichment (отложено, ≤8 часов)
- [ ] Регистрация Данияра на cooperation.uz (E-IMZO).
- [ ] Reverse-engineering `GetLotInfo` через Playwright + авторизованная сессия.
- [ ] Enrich 34K Лотов organization (огромная ценность для Bitrix-leads).

---

## Success Metrics

| Метрика | Baseline (28.04) | Цель | Метод проверки |
|---|---|---|---|
| Cooperation 7d records | 13,360 | 50,000+ (3.7×) | `SELECT count() WHERE source LIKE 'Cooperation%' AND collected_at >= now()-7d` |
| Dead Cooperation sources | 4 / 10 | 0 / 4 (после удаления 6 productName-блоков) | healthcheck `check_dead_sources` |
| Dead non-Cooperation sources | 12 | ≤3 (только TG low-signal в whitelist) | healthcheck |
| XT-Xarid тендеры 7d records | 0 | 1000+ | DB query |
| Yaml-строк под Cooperation | 393 | ~50 | `wc -l` блоков |
| Time-to-detect new dead source | 30+ дней (текущее) | 1 день | SLO check freshness |
| Quality dashboard answers "почему dead" | grep+SSH | Telegram-дайджест 09:00 | manual check |

---

## Anti-Scope

- **НЕ** строим Grafana/Loki — overkill для cron-based scraper.
- **НЕ** внедряем OpenTelemetry traces.
- **НЕ** трогаем `tenders.quality_score` (per-record качество — отдельная задача).
- **НЕ** партиционируем `source_quality_metrics` сразу — отложить до 50M+ строк.
- **НЕ** удаляем dead sources массово — soft-disable + auto-revive если данные вернутся.
- **НЕ** мигрируем feedback из JSONL в БД в этой итерации — отдельный таск (017_feedback.sql).
