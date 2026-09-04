"""Main runner: load sources, dispatch to adapters, gather results, save."""

import asyncio
import logging
import os
from typing import Dict, List, Tuple, Type

import yaml

from crawler.adapters.base import BaseAdapter
from crawler.core.db import upsert_tenders
from crawler.core.models import AdapterType, RawTender, SourceConfig, SourcesConfig

logger = logging.getLogger(__name__)

# Adapter registry — populated by register_adapter()
_adapter_registry: Dict[AdapterType, Type[BaseAdapter]] = {}


def register_adapter(adapter_type: AdapterType, cls: Type[BaseAdapter]) -> None:
    """Register an adapter class for a given AdapterType."""
    _adapter_registry[adapter_type] = cls


def load_sources(config_path: str) -> List[SourceConfig]:
    """Load and validate sources.yaml."""
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    config = SourcesConfig(**raw)
    enabled = [s for s in config.sources if s.enabled]
    logger.info(
        "Loaded %d sources (%d enabled) from %s",
        len(config.sources),
        len(enabled),
        config_path,
    )
    return enabled


def load_all_source_ids(config_path: str) -> set:
    """ВСЕ id из sources.yaml, включая `enabled: false`.

    `load_sources` отдаёт только включённые, а zero-result-трекеру нужно
    отличать «выключен руками» от «нет в конфиге вообще»: первых он молча
    пропускает, вторых — выкидывает из состояния как зомби.
    """
    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return {s.get("id") for s in (raw.get("sources") or []) if s.get("id")}


def _create_adapter(source: SourceConfig) -> BaseAdapter:
    """Create adapter instance for a source config."""
    cls = _adapter_registry.get(source.adapter)
    if cls is None:
        raise ValueError(
            "No adapter registered for type '%s' (source: %s)"
            % (source.adapter.value, source.id)
        )
    return cls(source)


async def _fetch_source(adapter: BaseAdapter) -> List[RawTender]:
    """Fetch from one source — wrapper for gather."""
    return await adapter.fetch()


async def run(
    config_path: str,
    dry_run: bool = False,
    source_ids: List[str] = None,
    lite: bool = False,
) -> Dict[str, int]:
    """Run the full crawl pipeline.

    1. Load sources from YAML
    2. Create adapter per source
    3. asyncio.gather all fetches
    4. Batch upsert to Supabase
    5. Return stats {source_id: count}

    Args:
        config_path: Path to sources.yaml
        dry_run: If True, don't write to DB
        source_ids: Optional filter — only run these source IDs
    """
    from crawler.core.crawl_logger import CrawlRunLogger

    _register_all_adapters()
    crawl_log = CrawlRunLogger(dry_run=dry_run, source_filter=source_ids)

    sources = load_sources(config_path)
    # Полный набор включённых — ДО фильтра прогона: краулы ходят разными
    # подмножествами (`0 */2` api, `30 */2` telegram), и набор прогона спрятал
    # бы telegram-молчунов от api-прогона в сводке источников.
    active_source_ids = {s.id for s in sources}
    try:
        known_source_ids = load_all_source_ids(config_path)
    except Exception as exc:
        logger.warning("load_all_source_ids failed: %s", str(exc)[:120])
        known_source_ids = set()
    if source_ids:
        sources = [s for s in sources if s.id in source_ids]
        logger.info("Filtered to %d sources: %s", len(sources), source_ids)

    if not sources:
        logger.warning("No sources to crawl")
        return {}

    # Create adapters — separate Telegram (sequential) from others (parallel)
    parallel_adapters: List[BaseAdapter] = []
    telegram_adapters: List[BaseAdapter] = []
    for src in sources:
        try:
            adapter = _create_adapter(src)
            if src.adapter == AdapterType.TELEGRAM:
                telegram_adapters.append(adapter)
            else:
                parallel_adapters.append(adapter)
        except ValueError as exc:
            logger.warning("Skipping source %s: %s", src.id, str(exc))

    # Log source starts
    for a in parallel_adapters + telegram_adapters:
        crawl_log.log_source_start(a.config.id)

    # Fetch non-Telegram in parallel
    tasks = [_fetch_source(a) for a in parallel_adapters]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Fetch Telegram sequentially (shared SQLite session file) with timeout.
    # 300s is a BACKSTOP only: the adapter self-limits via its 90s fetch budget and
    # returns partial results (cursor advances). A wait_for cancellation here loses
    # the whole channel result AND its cursor save — livelock on backlog (2026-07-18)
    # — so the backstop must stay comfortably above the adapter budget + one slow
    # in-flight AI call, never at the same order as the budget itself.
    tg_results = []  # type: List[object]
    for tg_adapter in telegram_adapters:
        try:
            tg_results.append(await asyncio.wait_for(tg_adapter.fetch(), timeout=300))
        except asyncio.TimeoutError:
            logger.error("[%s] Telegram fetch timed out (300s)", tg_adapter.config.id)
            tg_results.append(TimeoutError("Telegram fetch exceeded 300s"))
        except Exception as exc:
            tg_results.append(exc)

    all_adapters = parallel_adapters + telegram_adapters
    all_results = list(results) + tg_results

    # Collect results, stats, and per-source outcomes (RISK-1 / task #6).
    all_tenders: List[RawTender] = []
    stats: Dict[str, int] = {}
    # outcomes[sid] = {"count": int, "skipped_no_auth": bool, "error": Optional[str]}
    outcomes: Dict[str, Dict] = {}

    for adapter, result in zip(all_adapters, all_results):
        sid = adapter.config.id
        if isinstance(result, Exception):
            err = str(result)[:200]
            logger.error("[%s] Exception: %s", sid, err)
            stats[sid] = 0
            crawl_log.log_source_result(sid, 0, error=err)
            outcomes[sid] = {"count": 0, "skipped_no_auth": False, "error": err}
        else:
            stats[sid] = len(result)
            all_tenders.extend(result)
            crawl_log.log_source_result(sid, len(result))
            outcomes[sid] = {
                "count": len(result),
                "skipped_no_auth": getattr(adapter, "last_skipped_no_auth", False),
                "error": getattr(adapter, "last_error", None),
            }

    # Log summary
    total = sum(stats.values())
    source_log = " | ".join(
        "%s: %d" % (k, v) for k, v in stats.items()
    )
    logger.info("Crawl complete: %s -> Total: %d", source_log, total)

    # Cross-source exact dedup: sources sharing the same backend (e.g. hayotbirja.uz
    # and xt-xarid.uz return identical data via different domains) collapse to
    # one row per external_id. First-encountered source wins.
    group_by_source = {
        a.config.id: a.config.dedup_group for a in all_adapters if a.config.dedup_group
    }
    if group_by_source:
        seen_group_ids = {}  # type: Dict[Tuple[str, str], str]  # (group, external_id) -> kept source id
        deduped = []
        dropped_cross = 0
        for t in all_tenders:
            # Find this tender's source config (the adapter whose name matches t.source)
            adapter_id = next((a.config.id for a in all_adapters if a.config.name == t.source), None)
            group = group_by_source.get(adapter_id) if adapter_id else None
            if not group:
                deduped.append(t)
                continue
            key = (group, t.external_id)
            if key in seen_group_ids:
                dropped_cross += 1
                continue
            seen_group_ids[key] = t.source
            deduped.append(t)
        if dropped_cross:
            logger.info(
                "[Dedup] Cross-source exact dedup: %d -> %d (dropped %d duplicates across groups: %s)",
                len(all_tenders), len(deduped), dropped_cross,
                sorted(set(group_by_source.values())),
            )
            all_tenders = deduped

    # AI enrichment — fill missing fields (price, deadline, organization)
    from crawler.core.enricher import enrich_tenders

    enriched_count = await enrich_tenders(all_tenders)
    if enriched_count:
        logger.info("AI enriched %d tenders with missing fields", enriched_count)
    crawl_log.log_enrichment(enriched_count, ai_calls=enriched_count)

    # Upsert to Supabase
    upserted, new_tenders = await upsert_tenders(all_tenders, dry_run=dry_run)
    logger.info("Upserted %d / %d tenders to Supabase (%d new)", upserted, total, len(new_tenders))
    crawl_log.log_upsert(upserted, len(new_tenders))

    # Предмет лота у предквалификаций — ДО гейтов (22.08). Список GetLots
    # отдаёт только категорию, поэтому и ключевой гейт, и AI до сих пор судили
    # по названию рубрики: «Услуги издательские» на деле оказывались публикацией
    # статьи (349 млн, #7751), «Услуги печатные» — установкой баннеров (#7737).
    # Дыра двусторонняя: без предмета не только чужой лот проходит, но и НАШ не
    # проходит, если категория непрофильная. Отсюда место — перед send_alerts,
    # а не внутри него после префильтра.
    try:
        from crawler.core.prequal_detail import enrich as _enrich_prequal
        await _enrich_prequal(new_tenders, dry_run=dry_run)
    except Exception as exc:
        # Обогащение — улучшение, а не условие работы: его отказ не должен
        # останавливать доставку алертов.
        logger.warning("[Prequal] обогащение пропущено: %s", str(exc)[:150])

    # Deduplicate cross-source before alerting
    from crawler.core.dedup import group_for_alerts, load_recent_alerted_fingerprints

    recent_keys = load_recent_alerted_fingerprints(days=14) if not dry_run else set()
    deduped_new, group_sources = group_for_alerts(new_tenders, all_tenders, recent_alerted_keys=recent_keys)
    if len(new_tenders) != len(deduped_new):
        logger.info(
            "Dedup: %d new -> %d unique for alerts",
            len(new_tenders), len(deduped_new),
        )

    # Update group_id in DB for grouped tenders
    if group_sources and not dry_run:
        from crawler.core.dedup import find_groups
        groups = find_groups(all_tenders)
        if groups:
            await _update_group_ids(groups)

    # Send Telegram alerts for new matching tenders
    alerts_sent = 0
    if deduped_new:
        from crawler.core.notifier import send_alerts

        alerts_sent = await send_alerts(
            deduped_new, dry_run=dry_run, group_sources=group_sources,
        )
        if alerts_sent:
            logger.info("Sent %d Telegram alerts", alerts_sent)
        crawl_log.log_alerts(alerts_sent)

    # Check tender results (who won)
    if not dry_run and not lite:
        from crawler.core.results_tracker import update_results

        results_updated = await update_results(dry_run=dry_run)
        if results_updated:
            logger.info("Updated %d tenders with results", results_updated)

    # Check deadline reminders
    deadline_sent = 0
    if not dry_run and not lite:
        from crawler.core.deadline_tracker import check_deadlines

        deadline_sent = await check_deadlines(dry_run=dry_run)
        if deadline_sent:
            logger.info("Sent %d deadline reminders", deadline_sent)

    # Healthcheck — notify about new tenders or errors
    if not dry_run and not lite:
        from crawler.core.notifier import send_healthcheck

        errors = [
            sid for sid, result in zip(
                [a.config.id for a in all_adapters], all_results
            )
            if isinstance(result, Exception)
        ]
        await send_healthcheck(stats, len(new_tenders), alerts_sent, errors)

    # Zero-result tracker (task #6, RISK-1) — alerts sources that returned
    # nothing for 3+ consecutive cycles, recovery on return.
    try:
        if lite:
            raise RuntimeError("skip-in-lite")
        from crawler.core.zero_result_tracker import track_and_alert

        await track_and_alert(outcomes, dry_run=dry_run,
                              active_ids=active_source_ids,
                              known_ids=known_source_ids)
    except Exception as exc:
        # Never let the tracker crash the run — it's an observability signal.
        logger.warning("[ZeroResult] tracker error: %s", str(exc)[:120])

    # AI quality evaluation (daily)
    if not dry_run and not lite:
        from crawler.core.ai_evaluator import evaluate_crawl_quality

        await evaluate_crawl_quality(
            source_stats=stats,
            new_count=len(new_tenders),
            alerts_sent=alerts_sent,
            all_tenders=all_tenders,
            dry_run=dry_run,
        )

    # Seasonal predictions
    if not dry_run and not lite:
        from crawler.core.predictor import run_predictions

        predictions_stored = await run_predictions(dry_run=dry_run)
        if predictions_stored:
            logger.info("Stored %d new tender predictions", predictions_stored)

    # Quality tracking — snapshot + regression detection.
    # lite (частый reduction-прогон): snapshot НЕ пишем — subset-прогоны
    # каждые 20 мин затёрли бы baseline полного краула и регрессия-детектор
    # сравнивал бы яблоки с апельсинами.
    if lite:
        await crawl_log.finalize()
        return stats

    from crawler.core.quality_tracker import (
        QualitySnapshot, compare_snapshots, load_baseline, save_snapshot,
        flush_snapshot_to_supabase, profile_signature,
        bump_regress_streak, REGRESS_STREAK_WARN,
    )

    dedup_info = {
        "total": len(all_tenders),
        "groups": len(group_sources) if group_sources else 0,
        "duplicates": len(new_tenders) - len(deduped_new),
    }
    snapshot = QualitySnapshot.from_tenders(all_tenders, source_stats=stats, dedup_info=dedup_info)
    snapshot.total_new = len(new_tenders)
    snapshot.enriched = enriched_count
    snapshot.alerts_sent = alerts_sent
    snapshot.errors_count = len(crawl_log.errors)

    # Сравниваем с базой ТОГО ЖЕ набора источников (22.08). Раньше база была
    # одна на всех, а `0 */2` ходит по API-источникам, `30 */2` — по Telegram:
    # каждый прогон видел «чужую» базу и рапортовал катастрофу. 12 ложных
    # тревог в сутки, все в telegram-прогон.
    profile = profile_signature(stats.keys() if stats else [])
    if snapshot.overall.total == 0:
        # Пустой краул — «нет данных», а не «качество упало до нуля»: не
        # сравниваем и базу не трогаем (см. док-стринг save_snapshot).
        logger.info("Quality: пустой краул (profile=%s) — сравнение и база пропущены", profile)
        save_snapshot(snapshot, profile, update_baseline=False)
    else:
        baseline = load_baseline(profile)
        if baseline:
            report = compare_snapshots(baseline, snapshot)
            if report.has_regression:
                # ТОЛЬКО В ЛОГ. Поштучная отправка убрана 22.08 по решению
                # Данияра; настоящая поломка ловится healthcheck'ом (06:00,
                # alert-on-fail) и freshness_watchdog (07:00). С 01.09 глушим
                # и хлопки в логе: база — предыдущий крауль, одиночный спад —
                # шум выборки (29 WARNING за неделю, ноль настоящих поломок).
                # WARNING заслуживает только спад, держащийся подряд.
                streak = bump_regress_streak(profile, True)
                if streak >= REGRESS_STREAK_WARN:
                    logger.warning("Quality regression detected (profile=%s): %d краулов подряд\n%s",
                                   profile, streak, report.summary())
                else:
                    logger.info("Quality dip %d/%d (profile=%s): %s",
                                streak, REGRESS_STREAK_WARN, profile,
                                "; ".join("%s %+.1f" % (r.metric, r.delta)
                                          for r in report.regressions))
            else:
                bump_regress_streak(profile, False)
        else:
            logger.info("First quality snapshot for profile=%s (baseline)", profile)

        save_snapshot(snapshot, profile)
    if not dry_run:
        flush_snapshot_to_supabase(snapshot)
    logger.info("Quality score: %.1f (org=%.1f%% price=%.1f%% deadline=%.1f%%)",
        snapshot.overall_score(),
        snapshot.overall.pct("org"),
        snapshot.overall.pct("price"),
        snapshot.overall.pct("deadline"),
    )

    # Finalize crawl run log
    await crawl_log.finalize()

    return stats


async def _update_group_ids(groups: Dict[str, str]) -> None:
    """Update group_id column in Supabase for grouped tenders."""
    from crawler.core.db import _get_client
    try:
        client = _get_client()
        # Group by group_id to batch updates
        by_group = {}  # type: Dict[str, List[str]]
        for tender_id, group_id in groups.items():
            by_group.setdefault(group_id, []).append(tender_id)

        updated = 0
        for group_id, tender_ids in by_group.items():
            # tid format: "<adapter>-<external_id>"; parts[1] is the FULL external_id.
            # Batch one indexed `in_` UPDATE per group (exact match, no LIKE seq-scan).
            ext_ids = []
            for tid in tender_ids:
                parts = tid.split("-", 1)
                if len(parts) == 2:
                    ext_ids.append(parts[1])
            if not ext_ids:
                continue
            # Supabase caps URL length; chunk large groups to stay safe
            for i in range(0, len(ext_ids), 100):
                chunk = ext_ids[i:i + 100]
                try:
                    client.table("tenders").update(
                        {"group_id": group_id}
                    ).in_("external_id", chunk).execute()
                    updated += len(chunk)
                except Exception as exc:
                    logger.warning("[Dedup] Failed to update group_id: %s", str(exc)[:80])

        if updated:
            logger.info("[Dedup] Updated %d group_id records in DB", updated)
    except Exception as exc:
        logger.warning("[Dedup] DB update failed: %s", str(exc)[:80])


def _register_all_adapters() -> None:
    """Import and register all adapter types. Idempotent."""
    if _adapter_registry:
        return

    # API adapter
    try:
        from crawler.adapters.api import ApiAdapter

        register_adapter(AdapterType.API, ApiAdapter)
    except ImportError:
        logger.debug("API adapter not available")

    # HTML adapter
    try:
        from crawler.adapters.html import HtmlAdapter

        register_adapter(AdapterType.HTML, HtmlAdapter)
    except ImportError:
        logger.debug("HTML adapter not available")

    # SPA (Playwright) adapter
    try:
        from crawler.adapters.spa import SpaAdapter

        register_adapter(AdapterType.SPA, SpaAdapter)
    except ImportError:
        logger.debug("SPA adapter not available")

    # Telegram adapter — file does not exist yet (planned feature).
    # ImportError is expected and handled gracefully.
    try:
        from crawler.adapters.telegram_adapter import TelegramAdapter

        register_adapter(AdapterType.TELEGRAM, TelegramAdapter)
    except ImportError:
        logger.debug("Telegram adapter not available")

    # JSON-RPC adapter (hayotbirja, xt-xarid)
    try:
        from crawler.adapters.jsonrpc import JsonRpcAdapter

        register_adapter(AdapterType.JSONRPC, JsonRpcAdapter)
    except ImportError:
        logger.debug("JSON-RPC adapter not available")
