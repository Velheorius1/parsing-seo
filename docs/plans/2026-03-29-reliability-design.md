# Parsing-SEO Reliability & Self-Healing — Design Document

## Table of Contents
1. Overview
2. Key Decisions
3. Detailed Analysis (7 aspects)
4. Implementation Plan
5. Success Metrics

## Overview

Parsing-SEO monitors 50,037+ тендеров across 93 площадок Узбекистана. Система работает на двух машинах (VPS + Mac) с Supabase, Playwright, Telegram. Цель ревью: сделать систему fully self-healing с автопроверкой, логированием и автопочинкой всех компонентов.

**Ключевые находки:**
- Нет защиты от overlapping cron jobs (lock files отсутствуют)
- Standalone скрипты (contracts, UZEX) завершаются с exit 0 даже при полном сбое
- Mac — единственная точка отказа для cooperation + UZEX (но уже есть Vercel Edge proxy!)
- Detail-enriched данные (winner/discount) перезаписываются обычным crawl (last-writer-wins)
- E-IMZO JWT (5h TTL) истекает без уведомления
- Healthcheck покрывает 7 из ~12 компонентов

## Key Decisions

| # | Aspect | Decision | Confidence |
|---|--------|----------|------------|
| 1 | Cron reliability | flock guards + healthcheck Mac staleness | High |
| 2 | Self-healing | Add 5 missing checks: disk, Playwright, zombies, Mac staleness, Docker | High |
| 3 | Data integrity | Retry with backoff + timestamp guard в upsert (не перезаписывать старым) | High |
| 4 | Error handling | Exit codes + Telegram alerts в standalone scripts + retry decorator | High |
| 5 | Playwright resilience | Multi-selector fallback + content validation + zero-result alerting | High |
| 6 | Mac SPOF | Migrate to VPS via Vercel Edge proxy (already built!) | High |
| 7 | Auth lifecycle | Token expiry alerting в healthcheck + .gitignore fix | Medium |

## Detailed Analysis

### 1. Cron Reliability (Kelsey Hightower)

**Decision:** flock + healthcheck Mac staleness check

**Проблемы найдены:**
- Zero lock files / PID files в проекте
- Mac cron silently skips jobs when Mac sleeps
- Mac logs (`/tmp/`) grow unbounded, no rotation

**Что делать:**
- Добавить `flock -n /tmp/parsing-seo-crawl.lock` в `run_crawl.sh`
- Python lock decorator (fcntl) для standalone скриптов
- Log rotation: weekly truncate для Mac logs
- `@reboot` cron entry на VPS для немедленного crawl после перезагрузки

**Risks:** fcntl locks don't work across Docker containers

---

### 2. Self-Healing (Kelsey Hightower)

**Decision:** Option B — add 5 missing checks + expand auto-fix

**Healthcheck покрывает сейчас:** Supabase, freshness, sources, feedback bot, cron, Telegram, API endpoints (7 checks, 2 auto-fix)

**Добавить:**
1. `check_playwright()` — verify chromium binary exists. Auto-fix: `playwright install chromium`
2. `check_disk()` — `shutil.disk_usage("/")`. WARN 80%, FAIL 90%. Auto-fix: clean old JSONL >30 days
3. `check_zombie_processes()` — `pgrep -f chromium` older than 30min. Auto-fix: kill orphans
4. `check_mac_staleness()` — query cooperation/UZEX source `collected_at`. Alert if >12h stale
5. `check_docker()` — verify `tender-crawler` container is running

**Risks:** `playwright install chromium` downloads 130MB, could fail on slow connection

---

### 3. Data Integrity (Martin Kleppmann)

**Decision:** Retry with backoff + timestamp guard

**КРИТИЧЕСКАЯ ПРОБЛЕМА:** Detail-enriched `search_text` (winner, discount) перезаписывается обычным crawl. Last-writer-wins.

**Phase 1 (immediate):**
- Retry decorator (3 attempts, 1s/2s/4s backoff) для всех upsert функций
- Timestamp guard: `WHERE EXCLUDED.collected_at >= tenders.collected_at` — не перезаписывать свежее старым
- search_text guard: не перезаписывать если новый search_text короче существующего

**Phase 2 (next session):**
- Отдельная колонка `detail_data JSONB` для winner/start_price/discount
- Postgres function `smart_upsert_tender`

**Risks:** Supabase `.upsert()` не поддерживает custom ON CONFLICT — нужен `.rpc()`

---

### 4. Error Handling (Raymond Hettinger)

**Decision:** Exit codes + Telegram alerts + retry

**Проблемы:**
- `fetch_ebirja_contracts.py` и `fetch_uzex_auctions.py` завершаются exit 0 даже при 0 данных + ошибках
- Нет retry для transient failures (DNS, 503, 429)
- Standalone скрипты не пишут в `crawl_runs` (нет единого дашборда)
- Нет различия между "API вернул 0" и "connection failed"

**Что делать:**
1. Non-zero exit code когда fetch=0 AND errors occurred
2. `_send_alert(text)` helper для Telegram в standalone скриптах
3. Retry decorator (3 attempts, exponential backoff)
4. Интеграция с CrawlRunLogger (опционально)

**Priority:**
1. fetch_uzex_auctions.py — критично, на Mac, zero visibility
2. fetch_ebirja_contracts.py — аналогично
3. Retry decorator — shared utility

---

### 5. Playwright Resilience (Raymond Hettinger)

**Decision:** Multi-selector fallback + content validation + zero-result alerting

**Проблемы:**
- CSS selector `div.rounded-[16px]` — Tailwind utility, сломается при обновлении
- Blind `wait_for_timeout(5000)` вместо `wait_for_selector`
- Zero-result = INFO log, not ERROR (silent failure)

**Что делать:**
1. `wait_for_selector` вместо `wait_for_timeout` (multiple selectors, first match)
2. Content validation: extracted cards must match lot-number pattern `\d{6,}`
3. Zero-result = ERROR + Telegram alert
4. Retry wrapper (2 attempts) для `page.goto()` на timeout
5. Externalize selectors в config (как spa.py)

**Risks:** Tailwind class changes break all 3 fallback selectors simultaneously

---

### 6. Mac SPOF (Kelsey Hightower)

**Decision:** Migrate to VPS via Vercel Edge proxy

**КЛЮЧЕВОЕ ОТКРЫТИЕ:** Vercel Edge proxy уже существует (`/api/proxy/cooperation/route.ts`). fetch_cooperation.py уже умеет использовать прокси как fallback. Нужно только:

1. Добавить proxy support в `fetch_uzex_auctions.py` (сейчас нет)
2. Перенести оба скрипта в VPS cron
3. Убрать Mac cron entries
4. Добавить staleness monitor в healthcheck

**Vercel free tier:** 100k edge invocations/month. Текущий объём ~3600/month (1% лимита).

**Risks:**
- Cloudflare edge IPs могут быть заблокированы cooperation.uz в будущем
- Fallback: residential proxy $5/mo (если edge заблокируют)

---

### 7. Auth Lifecycle (Troy Hunt)

**Decision:** Token expiry alerting + .gitignore fix

**Проблемы:**
- E-IMZO JWT (5h TTL) истекает без уведомления — crawl silently skips ebirja
- `.env` (bare) NOT in .gitignore — risk of accidental commit
- Supabase access token (expires 27 Apr) — zero tracking

**Что делать:**
1. `check_tokens()` в healthcheck: alert когда E-IMZO <1h до expiry
2. Добавить `.env` в .gitignore (не только `.env.local`)
3. Planner task для renewal Supabase access token (до 27 Apr)
4. Pre-crawl token check: abort with Telegram alert вместо silent skip

**Risks:** E-IMZO auto-refresh невозможен без физического USB ключа

---

## Implementation Plan

### Phase 1: Critical fixes (2-3 hours)

- [ ] flock guard в `run_crawl.sh`
- [ ] Exit codes + Telegram alerts в `fetch_uzex_auctions.py` и `fetch_ebirja_contracts.py`
- [ ] Retry decorator (shared utility) для upsert и HTTP calls
- [ ] `.env` в .gitignore
- [ ] Zero-result alerting (0 contracts = ERROR + alert)

### Phase 2: Self-healing expansion (2-3 hours)

- [ ] healthcheck.py: add check_playwright, check_disk, check_zombie, check_mac_staleness, check_docker
- [ ] healthcheck.py: expand auto-fix (Playwright install, disk cleanup, zombie kill)
- [ ] Token expiry alerting (E-IMZO, Supabase access token)
- [ ] Content validation для Playwright selectors

### Phase 3: Mac → VPS migration (2-3 hours)

- [ ] Add proxy support to `fetch_uzex_auctions.py`
- [ ] Move cooperation + UZEX scripts to VPS cron
- [ ] Remove Mac cron entries
- [ ] Add staleness monitor в healthcheck
- [ ] Test end-to-end: VPS → Vercel Edge → cooperation.uz/UZEX → Supabase

### Phase 4: Data integrity (1-2 hours)

- [ ] Timestamp guard: не перезаписывать свежее старым
- [ ] search_text guard: не перезаписывать если новый короче
- [ ] Postgres migration: `detail_data JSONB` column (optional)

## Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Silent failure rate | ~30% (standalone scripts) | 0% (all failures alert) |
| Mac SPOF | Yes (2 scripts) | No (VPS via proxy) |
| Healthcheck coverage | 7/12 components | 12/12 |
| Auto-fix capabilities | 2 (feedback bot, stale crawl) | 7 (+ Playwright, disk, zombies, Docker, token) |
| Data integrity | Last-writer-wins (lossy) | Timestamp-guarded (preserving) |
| Mean time to detect failure | Hours (next healthcheck) | <30 min |
| Lock file protection | 0 scripts | All scripts |
| Log rotation | Partial (VPS only) | Full (VPS + Mac) |
