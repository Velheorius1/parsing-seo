# Alert Signal-vs-Noise — Design Document (2026-07-01)

*From /deep-think: 5 parallel expert analyses (Newman, Winand, Kleppmann, Hunt, Eyal+Karpathy), grounded on 2400 real alerts.*

## Table of Contents
1. Overview & unified diagnosis
2. Key decisions
3. Detailed analysis (5 aspects)
4. Implementation plan (3 phases)
5. Success metrics
6. Risks

---

## 1. Overview & unified diagnosis

Daniyar (Winch, a printing house) gets **~130–208 alerts/day** and most are noise he must check by hand. The screenshots were "Бланк" lots from **XT-Xarid э-магазин**, including **his own already-won lot**. The 5 experts converged on **three structural root causes** — all verified in code + live data:

1. **Wrong side of the market (~33% of alerts).** E-shop sources (XT-Xarid э-магазин 26%, UZEX Э-магазин ~7%) are **supplier catalogs** (`ref_online_shop_public`, `SO`=Sale Offer) — *sellers* post what they offer. There is **no buyer** and **no possible demand signal** (live probe: no bids/participants field). The `organization` field is a *seller* (XT-Xarid `vendor`) or a *country* (UZEX/Ebirja) — never a buyer. Winch's own postings live here → the self-alert. Mining these for buyer demand is structurally impossible.
2. **Duplicates (40%, worse than the felt "1/3").** Every repost mints a **new physical id** (`external_id = message.id` for Telegram; new GUID per crawl for birja) → a **new row** → re-alerts. The dedup layer **fails on empty-org rows** (all TG leads) and on one-word title edits. eco print ×89, greeting ×50, чек-лента ×37, komron ×24.
3. **Unimodal delivery for a bimodal job.** 200 equal-weight pushes. But the job splits: **(A)** "don't miss a winnable order" — a few/day, time-critical, *deserves* an interrupt; **(B)** "stay aware" — ~180/day, skimmable, does *not* want 180 buzzes. Pushing B through A's channel killed attention *and* feedback (0 clicks/2mo).

**Plus a wasted asset:** the *real* demand signal — `part_count` (live bidders, e.g. "5 торгуются, до закрытия 12 мин") — **exists on reverse auctions & RFPs but is fetched and thrown away** (jsonrpc.py:279 → never reaches the model).

**The fix is not "filter harder" (that risks missing the 5% winnable orders) — it's to route by market-side + logical-identity + signal-tier.**

---

## 2. Key decisions

| # | Aspect | Decision | Confidence |
|---|--------|----------|------------|
| 1 | Source taxonomy | 3 buckets: **ACT**→push, **MAYBE**→digest, **NOISE(e-shop/completed/self)**→drop-push. E-shop is sell-side noise. | High |
| 2 | Demand signal | Demand-gate on e-shop is **impossible** (no field). Filter e-shop **by type**; **surface `part_count`** on auctions/RFPs (it's real & discarded). | High |
| 3 | Duplicates (40%) | Persisted **`content_key` hash** (fail-closed, stable w/o org) + **cooldown** (14d tenders / 30d spam) + material-change escape. | High |
| 4 | Own/competitor | 8-line **own-org allowlist** (`winch`/`винч`) at top of `send_alerts`. Competitor registry rejected (redundant w/ #1). | High |
| 5 | Volume/delivery | **Split PUSH vs ranked DIGEST** (value×demand×freshness). **Feedback→auto-mute** with **✅-veto** recall guard, weekly AI-judge audit. | High |

---

## 3. Detailed analysis

### Aspect 1 — Source taxonomy (Sam Newman)
**Decision:** classify by *demand-direction* at the source, route on it (push/digest/drop). Buckets:
- **ACT → push:** TG PR Media leads (buyer asking now), reverse auctions (live window), UZEX Предквалификации, ETender, Cooperation Лоты/plans, RFPs, ministry/bank tenders.
- **MAYBE → digest:** UZEX Э-магазин бумага/издательские/печатные (rare real demand).
- **NOISE → drop-push:** **XT-Xarid э-магазин** (pure supplier catalog; twin `hayotbirja-shop` already disabled), completed deals (E-Birja/ETender Сделки — already awarded), own lots.
**Evidence:** `message_type="tender"` default → e-shop gets full push; no digest tier exists; config comment on `xt-xarid-shop` = "объявления поставщиков, госмагазин". **Risk:** `part_count` semantics — verify before trusting as a gate.

### Aspect 2 — Demand signal (Markus Winand)
**Decision:** `bid_count` is **dead code** (computed jsonrpc.py:279, never set on `RawTender`, field absent from model) → the "617×0" is a phantom. Live probe: e-shop `ref_online_shop_public` has **no** bid/participant field; reverse-auction `ref_reduction_object_public` returned **`part_count=5, remain 696s`**. So: **(B)** filter e-shop by type (no demand gate possible); **(C)** add `part_count`/`bid_count` to the model and **render it on auctions/RFPs** ("N торгуется, закрытие через T"), like ETender already shows participants. Don't fully kill e-shop (rare: Winch's own offer + a real buyer) → demote to digest. **Risk:** demand feeds are thin (print RFPs ~1–2/mo) — demote e-shop, don't delete.

### Aspect 3 — Duplicates 40% (Martin Kleppmann)
**Decision:** duplicates = many rows / one logical lot (a row can't re-alert). 3 holes: **(1 dominant)** empty-org rows are un-dedupable (`_within_source_key` seeds key with unique row-id; alerted empty-org rows skipped from fingerprint store) — all TG spam bypasses suppression; **(2)** exact word-set fingerprint forks on "80мм"; **(3)** the 7-day window (weakened in `27e58be` for a valid recall reason). Fix = persisted **`content_key = sha1(source_class · norm_org · title_core[spec-stripped] · price_bucket · deadline_day)`**, **fails closed to a stable hash** (never a row-id). Suppress if same key alerted within **cooldown** (14d tenders / **30d spam**) **and** price+deadline unchanged; allow genuine re-tenders (material change) — preserves the never-alerted-predecessor recall fix. Fuzzy ≥0.5 as fallback. **Risk:** false-merge of distinct SKUs — mitigated by price+deadline in the key + logged suppressions for a week.

### Aspect 4 — Own/competitor (Troy Hunt)
**Decision:** e-shop `organization` is seller/country, never a reliable buyer. Add an **own-org allowlist** (normalized substring: `winch`, `винч`, full ЧП legal form — **not** bare surname, collision risk) at the top of `send_alerts`, logged. **Reject** a competitor registry (high-maintenance, redundant with #1 dropping e-shop by type). **Risk:** suppressing a real buyer sharing a fragment — `winch` is rare in UZ procurement; logged for audit.

### Aspect 5 — Delivery + feedback learning (Nir Eyal + Karpathy)
**Decision:** **split delivery** — PUSH for high-signal (hot leads, reverse-auction live window, score≥85 & high price, deadline<48h); **ranked DIGEST** for the rest (top-10 by value(log price)×demand(score×source-weight)×freshness(deadline), tail collapsed to "+N", hourly for auctions / 2×day general). **Feedback→auto-mute:** `mute_rules` table; each ❌ attributes to patterns (source/type/org/keyword); promote at `❌≥3 AND ✅==0 AND ❌_rate≥0.8`; graduated action (digest-only → collapsed → mute), **one ✅ permanently vetoes** a mute; weekly AI-judge audits would-be-muted items for recall; **never stop ingestion** (mute = delivery decision, reversible). Bulkhead: digest failure must never break the hot-lead push (fail-open). **Risk:** silent over-mute — ✅-veto + graduated tiers + CI recall-regression test on known-good fixtures.

---

## 4. Implementation plan

### Phase 1 — Quick wins (this week, low-risk, ~⅔ of the noise)
- [ ] **Demote e-shop from push.** Add XT-Xarid э-магазин (+ UZEX Э-магазин рекламные, off-profile) to a `_NO_PUSH_SOURCES` set in `notifier.send_alerts` → not pushed, still crawled/stored (visible on Vercel). Reversible. *(kills 26%+)*
- [ ] **Own-lot suppressor.** 8-line `_is_own_lot(org)` allowlist at top of `send_alerts`, logged `[Self-Lot]`.
- [ ] **`content_key` dedup + cooldown.** Add `content_key` column (backfill once); compute in `upsert_tenders`; replace `_within_source_key` empty-org fallback with the fail-closed hash; suppress within cooldown (14d/30d spam) unless price/deadline changed. Ship with the empty-org repost test (Dodds). *(kills the bulk of 40% dups)*

### Phase 2 — Structural (next)
- [ ] **3-tier routing** (`actionability: act|maybe|noise` in source config) + a **ranked digest** builder (separate scheduled job, bulkheaded; top-10 + "+N", hourly auctions / 2×day general). E-shop MAYBE → digest.
- [ ] **Surface `part_count`.** Add `bid_count`/`part_count` to `RawTender`; wire jsonrpc.py:279 through; render "🔨 N торгуется · закрытие через T" on reverse auctions + RFPs (extend the 🔄 Обратный тендер label).

### Phase 3 — Learning (compounds P1/P2)
- [ ] **`mute_rules`** table + pattern attribution in `record_feedback` + graduated auto-mute with ✅-veto.
- [ ] Wire promotion/demotion + **AI-judge recall audit** into the weekly routine (`ROUTINE.md`); report auto-mutes in the weekly Telegram digest for confirmation.
- [ ] CI recall-regression test at the suppression boundary.

---

## 5. Success metrics
- **Volume:** ~200/day → target **≤60/day pushed** (rest in digest); e-shop push = 0.
- **Duplicate rate:** 40% → **<10%** of pushed alerts.
- **Self-lots pushed:** → **0**.
- **Precision (weekly AI-judge):** 7.0 → **≥8.5**.
- **Engagement:** feedback clicks/week from ~0 → **>0** (channel worth opening ⇒ clicks return).
- **Recall guard (must not regress):** 0 winnable orders missed — validated by the weekly AI-judge audit over would-be-suppressed items.

---

## 6. Cross-cutting risks
- **Over-suppression is silent & catastrophic** (missed winnable order). Guards everywhere: fail-open push path, ✅-veto, cooldown material-change escape, never-stop-ingestion, weekly AI-judge audit, CI recall test. This gets the most engineering.
- **`part_count` semantics** (bidders vs offers) — surface as display first, harden into a gate only after observing live behavior.
- **Thin demand feeds** — demote e-shop (digest), never delete; a rare real order in an e-shop stays visible on pull.
