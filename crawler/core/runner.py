"""Main runner: load sources, dispatch to adapters, gather results, save."""

import asyncio
import logging
import os
from typing import Dict, List, Type

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
    _register_all_adapters()

    sources = load_sources(config_path)
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

    # Fetch non-Telegram in parallel
    tasks = [_fetch_source(a) for a in parallel_adapters]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Fetch Telegram sequentially (shared SQLite session file)
    tg_results = []  # type: List[object]
    for tg_adapter in telegram_adapters:
        try:
            tg_results.append(await tg_adapter.fetch())
        except Exception as exc:
            tg_results.append(exc)

    all_adapters = parallel_adapters + telegram_adapters
    all_results = list(results) + tg_results

    # Collect results and stats
    all_tenders: List[RawTender] = []
    stats: Dict[str, int] = {}

    for adapter, result in zip(all_adapters, all_results):
        sid = adapter.config.id
        if isinstance(result, Exception):
            logger.error("[%s] Exception: %s", sid, str(result))
            stats[sid] = 0
        else:
            stats[sid] = len(result)
            all_tenders.extend(result)

    # Log summary
    total = sum(stats.values())
    source_log = " | ".join(
        "%s: %d" % (k, v) for k, v in stats.items()
    )
    logger.info("Crawl complete: %s -> Total: %d", source_log, total)

    # Upsert to Supabase
    upserted, new_tenders = await upsert_tenders(all_tenders, dry_run=dry_run)
    logger.info("Upserted %d / %d tenders to Supabase (%d new)", upserted, total, len(new_tenders))

    # Deduplicate cross-source before alerting
    from crawler.core.dedup import group_for_alerts

    deduped_new, group_sources = group_for_alerts(new_tenders, all_tenders)
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

    # Check tender results (who won)
    if not dry_run:
        from crawler.core.results_tracker import update_results

        results_updated = await update_results(dry_run=dry_run)
        if results_updated:
            logger.info("Updated %d tenders with results", results_updated)

    # Check deadline reminders
    deadline_sent = 0
    if not dry_run:
        from crawler.core.deadline_tracker import check_deadlines

        deadline_sent = await check_deadlines(dry_run=dry_run)
        if deadline_sent:
            logger.info("Sent %d deadline reminders", deadline_sent)

    # Healthcheck — notify about new tenders or errors
    if not dry_run:
        from crawler.core.notifier import send_healthcheck

        errors = [
            sid for sid, result in zip(
                [a.config.id for a in all_adapters], all_results
            )
            if isinstance(result, Exception)
        ]
        await send_healthcheck(stats, len(new_tenders), alerts_sent, errors)

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
            # tender_id here is the RawTender.id (prefixed), match by external_id pattern
            # We need to update by matching the tender's external_id+source combo
            # For now, update by external_id prefix match
            try:
                for tid in tender_ids:
                    # tid format: "etender-12345" — split to get source prefix + external_id
                    parts = tid.split("-", 1)
                    if len(parts) == 2:
                        client.table("tenders").update(
                            {"group_id": group_id}
                        ).like("external_id", "%s%%" % parts[1]).execute()
                        updated += 1
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

    # Telegram adapter
    try:
        from crawler.adapters.telegram_adapter import TelegramAdapter

        register_adapter(AdapterType.TELEGRAM, TelegramAdapter)
    except ImportError:
        logger.debug("Telegram adapter not available")
