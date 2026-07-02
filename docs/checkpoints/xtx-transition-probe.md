# V0 probe — e-shop↔auction linkage + get_proc semantics (2026-07-02, live API)

## Findings (all verified live via api.xt-xarid.uz / api.hayotbirja.uz)

1. **Reduction listing** (`ref_reduction_object_public`, exact adapter shape `method:"ref", params:{ref,op:"read",limit,offset}`): keys = `area, close_at, company_id, contract_pay_percent, currency, good_count, good_list, green, id, inserted_at, last_price, meta, part_count, publicated_at, remain_time, start_price, status`. Live sample: `id=7803257 status=publicated part_count=6 remain_time=488`.
2. **NO link field to the originating e-shop position** — no shop/position/origin/parent/offer key in listing or get_proc. → V2 must use a client-side join (good_list names × e-shop product_name+vendor), confidence-tagged.
3. **`get_proc` (urpc, anonymous) works on xt-xarid AND hayotbirja** (shared backend — same lot id exists on both):
   - live lot → 21 keys, `status=publicated` (but `remain_time=None` — remain lives in the LISTING only);
   - **closed lot → `status="close"`** (22 keys) — the verifier signal;
   - very old/purged lot → 7-key stub, `status=None` → treat as *unverifiable* (fail-open), NOT gone;
   - nonexistent id → explicit error object (`error_number`, `message`) → *gone*.
   - `purchase_positions` = buyer's procurement plan positions (goods, prices) — rich context for the investigator (V4).
4. **Name-join first attempt:** `good_list[0].name` was empty on the probe sample — goods names live in nested structure (use adapter's `_build_goods_title`/meta.good_maps extraction). Join feasibility unproven on 1 sample; V2 ships as enrichment-only with confidence tag, never a filter.

## Decisions for V1/V2
- **V1 verifier (birja):** `get_proc` by external_id → `status in (open, publicated)` → OK; `status in (close, closed, cancel, cancelled, not_realized)` → CLOSED (drop); explicit not-found error → GONE (drop); missing status / network error → UNVERIFIABLE (send, fail-open).
- **V2:** reduction feed = the auction-started event (cron `*/20`); enrich with `remain_time` countdown (listing field, fetched but unused) + best-effort e-shop join.
