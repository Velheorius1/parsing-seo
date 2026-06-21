# Link audit — 2026-06-21 (browser-verified, anonymous)

Method: pulled real `source_url` per source from Supabase, navigated each in a real browser (Playwright), screenshotted, recorded outcome. 11 distinct URL patterns across every UZEX/xt-xarid/etender/cooperation family + old-vs-new route head-to-head for the broken ones.

## Verdicts

| # | Source | id tested | URL pattern | Result |
|---|--------|-----------|-------------|--------|
| 1 | UZEX Предквалификации | 77521 | `new-xarid…/home/shop/detail/{id}` (current template) | ❌ **wrong card** — deleted 2021 "Лопата" (shovel), e-shop id-space |
| 2 | UZEX Предквалификации | 77521 | `xarid.uzex.uz/prequalification/detail/{id}` (DB legacy) | ❌ redirects to homepage |
| 3 | UZEX Предквалификации | 77521 | `new-xarid…/home/purchase/proposal-request/detail/{id}` | ✅ **correct** — "Кокс и нефтепродукты" №261291380077521, Опубликован, 150M, taklif button |
| 4 | UZEX Обратные аукционы | 914 | `xarid.uzex.uz/auction/detail/{id}` (DB legacy) | ✅ **correct** — valid auction, product table |
| 5 | UZEX Э-магазин | 27183976 | `xarid.uzex.uz/shop/lot-details/{external_id}` (current template) | ❌ **"no data found"** toast (xarid-api-shop GetLot dead) — *the user's screenshot* |
| 6 | UZEX Э-магазин | 27183976 | `new-xarid…/home/shop/detail/{external_id}?elektron=true` | ✅ **correct** — "Книги печатные" SO27183976, Услуги издательские, active to 17.08 |
| 7 | Xarid Прямые закупки | 4419497 | `new-xarid…/home/shop/detail/{id}` (current template) | ❌ **wrong card** — expired 2023 "Питательные среды" (id=display_id, not e-shop id) |
| 8 | XT-Xarid (reverse/tender) | 7749217 | `xt-xarid.uz/procedure/{external_id}/core` | ✅ correct — Эълон №7749217, display-stand products |
| 9 | ETender UZEX | 496165 | `etender.uzex.uz/lot/{id}` | ✅ correct — Tender №26121006496165, active |
| 10 | Cooperation.uz Лоты | SL1554184 | `new.cooperation.uz/supplier/lots?lotId=…` | ❌ 404 — **but** source is broken-spa → alert uses Vercel archive instead (OK) |
| 11 | Vercel archive | uuid | `parsing-seo.vercel.app/tenders/{uuid}` | ✅ renders DB snapshot (fallback works) |

## Root cause
The 2026-05-21 "universal new-xarid migration" rewrote several UZEX templates to `home/shop/detail/{id}?elektron=true` on the theory that one e-shop URL works for all lot types. **It doesn't** — that route only resolves e-shop *product* ids. Prequalification/auction/direct ids fed into it resolve to unrelated/deleted e-shop products. The fix was applied to `xarid-competitions` (→ `proposal-request/detail`) but **not** to prequest/auctions/direct. The `xarid-competitions` comment (`crawler/config/sources.yaml:184-187`) literally documents "old /home/shop/detail/{id} used the e-shop id-space → wrong card" yet the sibling sources kept it.

Mechanics: `_tender_to_row` always writes `source_url` (`crawler/core/db.py:44`); upsert `on_conflict=external_id,source` overwrites it every crawl; adapter rebuilds it from the template (`crawler/adapters/api.py:811`). So **fixing the template auto-corrects future alerts** on the next successful crawl. `use_proxy` sources (auctions/prequest) crawl intermittently → legacy URLs linger in DB, but new alerts use the live template. Already-sent Telegram messages can't be edited (no backfill possible/needed there).

## Fix map (Phase 1)

| Source | sources.yaml line | → New template |
|--------|-------------------|----------------|
| `uzex-prequest` | 1155 | `https://new-xarid.uzex.uz/home/purchase/proposal-request/detail/{id}` ✅ verified |
| `uzex-auctions` | 1126 | `https://xarid.uzex.uz/auction/detail/{id}` ✅ verified |
| `uzex-shop-print` | 981 | `https://new-xarid.uzex.uz/home/shop/detail/{external_id}?elektron=true` ✅ verified |
| `uzex-shop-paper` | 1016 | (same as above) |
| `uzex-shop-publish` | 1051 | (same as above) |
| `uzex-shop-adv` | 1086 | (same as above) |
| `xarid-direct` | 235 | **No change** — already in `BROKEN_SPA_SOURCES` (crawler/core/snap.py:34), so the wrong-card URL is never shown; alert uses Vercel archive + number-search handoff. Template left as-is. |

Screenshots saved this session (worktree root): `prequest-NEW-shop-detail-77521`, `prequest-PROPOSAL-77521`, `auction-OLD-914`, `eshop-knigi-27183976`, `eshop-NEW-27183976`, `direct-4419497`, `xtxarid-7749217`, `etender-496165`, `cooperation-SL1554184`, `vercel-archive-3712`.

## Link-integrity score (for control measurement)
Of 7 actively-linked source families: **3 fully broken** (prequest, e-magazin×4 as one family, direct), **1 legacy-broken-but-fixable** (auctions), **3 working** (xt-xarid, etender, cooperation-via-archive). Counting the alert-facing families: working = xt-xarid, etender, cooperation(archive), competitions = 4/8; broken = prequest, auctions, e-magazin, direct = 4/8 → **link integrity ≈ 50%** pre-fix. Post-fix (prequest/auctions/e-magazin verified): **7/8 ≈ 88%** (direct pending).
