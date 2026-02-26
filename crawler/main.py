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


if __name__ == "__main__":
    main()
