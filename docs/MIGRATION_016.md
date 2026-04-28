# Migration 016: source_quality_metrics

**Status:** Pending manual application (no direct DB access from VPS)

## What it adds
- `source_quality_metrics` — long-format per-source metrics (org_pct, price_pct, items_fetched, quality_score, etc.)
- `source_quality_daily` — daily rollup table (forever retention)
- `source_quality_latest` — convenience view (latest metric per source/type)
- 3 indexes: trend per source, top-N worst, partial zero-fetch SLA

## How to apply

1. Open https://supabase.com/dashboard/project/oaoehczbycrabkprazts/sql/new
2. Paste contents of `supabase/migrations/016_source_quality_metrics.sql`
3. Click "Run"
4. Verify: SQL Editor → `SELECT count(*) FROM source_quality_metrics;` should return 0

## After migration
The next crawl run will start writing metrics. Verify after first cron tick:

```sql
SELECT source, metric_type, metric_value, computed_at
FROM source_quality_metrics
ORDER BY computed_at DESC
LIMIT 20;
```

## Graceful fallback
If migration is not applied, `flush_snapshot_to_supabase()` logs a warning
and returns 0 — pipeline continues uninterrupted. JSONL backups
(`logs/quality_history.jsonl`) keep working as before.
