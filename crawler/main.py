"""Crawler entrypoint: load config, run all adapters, save results."""

import argparse
import asyncio
import logging
import os
import sys

from crawler.config.settings import settings
from crawler.core.runner import run


def setup_logging(level: str) -> None:
    """Configure logging format."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Tender crawler")
    parser.add_argument(
        "--config",
        default=os.path.join(os.path.dirname(__file__), "config", "sources.yaml"),
        help="Path to sources.yaml",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Don't write to Supabase, just log results",
    )
    parser.add_argument(
        "--sources",
        nargs="*",
        default=None,
        help="Only run specific source IDs (space-separated)",
    )
    parser.add_argument(
        "--deadlines-only",
        action="store_true",
        default=False,
        help="Only check deadline reminders, skip crawling",
    )
    parser.add_argument(
        "--competitors",
        action="store_true",
        default=False,
        help="Run competitor scan (UZEX deals + cooperation.uz)",
    )
    parser.add_argument(
        "--log-level",
        default=settings.log_level,
        help="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )
    args = parser.parse_args()

    setup_logging(args.log_level)
    logger = logging.getLogger("crawler")

    dry_run = args.dry_run or settings.dry_run
    if dry_run:
        logger.info("DRY RUN mode — no database writes")

    # Competitor scan mode
    if args.competitors:
        from crawler.scripts.competitor_scan import main as competitor_main

        logger.info("Running competitor scan...")
        competitor_main()
        return

    # Deadline-only mode: just check reminders, no crawling
    if args.deadlines_only:
        from crawler.core.deadline_tracker import check_deadlines

        logger.info("Checking deadline reminders...")
        sent = asyncio.run(check_deadlines(dry_run=dry_run))
        logger.info("Deadline reminders sent: %d", sent)
        return

    logger.info("Starting tender crawler...")
    stats = asyncio.run(
        run(
            config_path=args.config,
            dry_run=dry_run,
            source_ids=args.sources,
        )
    )

    # Print summary
    total = sum(stats.values())
    logger.info("=== CRAWL COMPLETE ===")
    for source_id, count in sorted(stats.items()):
        logger.info("  %s: %d tenders", source_id, count)
    logger.info("  TOTAL: %d tenders", total)

    # Write healthcheck marker for Docker HEALTHCHECK
    try:
        with open("/tmp/last_crawl_ok", "w") as f:
            from datetime import datetime
            f.write(datetime.utcnow().isoformat())
    except Exception:
        pass


if __name__ == "__main__":
    main()
