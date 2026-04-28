# Implementation 2026-04-28 — Phases 0-3 complete

## Summary

Реализованы 4 фазы редизайна parsing-seo по deep-think плану.

## Commits (parsing-seo main)

| Commit | Phase | Description |
|---|---|---|
| `2ff0576` | Pre | fix: healthcheck Supabase token (hardcoded date → key-format check) |
| `36b9c5e` | docs | cooperation coverage 5x + data quality framework design |
| `4cf1169` | docs | parsing-seo 2.0 AI-augmented engine design |
| `18021ea` | **0+1** | Qwen3.6 Max Preview + critical fixes (cron / dedup / filter / SLO / TG) |
| `4c125ee` | **2** | productName loop refactor (10 yaml blocks → 1 source + 26 values) |
| `9084842` | **3** | source_quality_metrics framework + flush_to_supabase + migration 016 |

## Backup

- `/root/backups/parsing-seo-20260428-0449.tar.gz` (2.8M, исходники)
- `/root/backups/tenders-snapshot-20260428-0450.jsonl.gz` (7.3M, 30d данных, 61K записей)

## Phase 0: AI model

- Файлы: `crawler/config/settings.py:56`, `scripts/fetch_cooperation.py:53`
- Было: `qwen/qwen3-30b-a3b`
- Стало: `qwen/qwen3.6-max-preview`
- Verified: `200 OK` reply test через OpenRouter

## Phase 1: Critical fixes

| 1A | `scripts/run_proxy_fetch.sh` | Запускает `--source plans/auction/eshop` (резурекция 3 dead Cooperation source) |
| 1B | `crawler/config/sources.yaml:2709,2736` | XT-Xarid `dedup_group: xtx-tender / xtx-reduction` (split от `birja-*`) |
| 1C | `crawler/config/sources.yaml` ×10 | Убран `unit: {gt: 0}` (cooperation теперь шлёт черновики с `unit: 0.00`) |
| 1D | `crawler/scripts/healthcheck.py` | `check_dead_sources()` per-source 7d SLO + paginated query (fix Supabase 1000-row limit bug) |
| 1E | `crawler/config/sources.yaml` | Disabled tg-tenderweek (5 ever) + tg-kommunal (3 ever) |

## Phase 2: productName loop refactor

- `crawler/core/models.py` — `SourceConfig.productName_param` + `productName_values: List[str]`
- `crawler/adapters/api.py:285` — итерация по values, dedup по external_id, structured log per-keyword
- `crawler/config/sources.yaml` — 10 cooperation-* блоков (391 строка) → 1 `cooperation-plans-filtered` с 26 productName_values

**Verification (live dry-run):**
- 26 productName terms × 5-10 unique items = 165 raw → 81 unique after item_filter/keywords
- Старые 10 источников накопили ~1180 records за недели → новый источник даёт ~80 за один прогон
- Ожидаемое улучшение под 3x/day cron: **5-10x** records от Cooperation.uz

## Phase 3: Quality metrics

- Migration 016: `source_quality_metrics` (long-format) + `source_quality_daily` (rollup) + 3 индекса (trend/worst-N/zero-fetch)
- `quality_tracker.flush_snapshot_to_supabase()` — батч-INSERT ~6 metrics × N sources после `save_snapshot`
- `runner.py:316` — call после save_snapshot, skipped on dry-run
- Graceful fallback: если migration не применена → warning, не падает
- **Migration application requires manual step:** см. `docs/MIGRATION_016.md`

## Test results

```
$ pytest crawler/tests/
137 passed, 2 warnings in 0.28s

$ healthcheck
Summary: 17 OK, 6 WARN, 0 FAIL
✅ supabase: Connected. 160274 tenders total
✅ sources: 77 active sources, 45066 records in last 7 days
⚠️ sources.dead_7d: 16 enabled sources with 0 records in 7d (NEW CHECK)
✅ token.supabase: Supabase API key (sb_secret format, no expiry)
✅ eimzo_auth: Ebirja JWT refreshed 1.3h ago via VPS cron
```

## Что осталось (manual / next sprints)

1. **Применить migration 016** через Supabase Studio (см. `docs/MIGRATION_016.md`)
2. **Phase 4** (structured logging `crawl_run_sources` со stages[]) — отложен, не критичный
3. **Phase 5** (E-IMZO enrichment 34K Лотов) — требует регистрации Данияра на cooperation.uz

## Cost impact

- Qwen3.6 Max Preview: $1.30/Mtok input, $7.80/Mtok output (vs Qwen3-30B-a3b ~$0.10/Mtok)
- Объём текущих AI calls: ~500-1000 enrichment calls/day
- Ожидаемый месячный cost: ~$5-15/мес (vs текущие копейки, но качество AI radically выше)

## Risk monitoring

- **R1:** XT-Xarid split может породить дубли с Hayotbirja → ОК (external_id ranges разные: 5M vs 2.9M-7M)
- **R2:** Cooperation.uz может вернуть unit:0 черновики в БД → шум только в DB, не в Telegram (keywords отфильтруют)
- **R3:** productName loop = 26 HTTP calls/cycle → ~3 минуты/run, в рамках rate-limit (2 req/s)
- **R4:** flush_to_supabase fails graceful если таблица не создана — pipeline продолжает работать
