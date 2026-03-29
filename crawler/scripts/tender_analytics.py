#!/usr/bin/env python3
"""Аналитика тендеров: победители, средняя скидка, активность по компаниям.

Источники данных:
- ebirja-auctions-active: total_sum (старт) vs current_price (текущая ставка)
- ebirja-ext-contracts: завершённые сделки (productName, price, totalPrice)
- Все тендеры: группировка по organization

Usage:
    python -m crawler.scripts.tender_analytics [--top N]
"""

import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)


def get_client():
    """Init Supabase client."""
    from crawler.config.settings import settings
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def _fetch_all(client, table, select, filters=None, limit_per_page=1000):
    # type: (any, str, str, list, int) -> list
    """Fetch all rows with pagination (Supabase returns max 1000 per request)."""
    all_data = []
    offset = 0
    while True:
        q = client.table(table).select(select)
        if filters:
            for f in filters:
                q = f(q)
        result = q.range(offset, offset + limit_per_page - 1).execute()
        if not result.data:
            break
        all_data.extend(result.data)
        if len(result.data) < limit_per_page:
            break
        offset += limit_per_page
    logger.info("_fetch_all: fetched %d rows from %s", len(all_data), table)
    return all_data


def _get_count(client, table, select="*", filters=None):
    # type: (any, str, str, list) -> int
    """Get exact row count without fetching all data."""
    q = client.table(table).select(select, count="exact")
    if filters:
        for f in filters:
            q = f(q)
    result = q.limit(0).execute()
    return result.count or 0


def analyze_by_organization(client, top_n=20):
    """Top organizations by number of tenders."""
    data = _fetch_all(
        client, "tenders", "organization, source",
        filters=[
            lambda q: q.not_.is_("organization", "null"),
            lambda q: q.neq("organization", ""),
        ]
    )

    if not data:
        print("No data found.")
        return

    # Count by organization
    org_counts = {}  # type: dict
    org_sources = {}  # type: dict
    for row in data:
        org = (row.get("organization") or "").strip()
        if not org or len(org) < 3:
            continue
        org_counts[org] = org_counts.get(org, 0) + 1
        src = row.get("source", "")
        if org not in org_sources:
            org_sources[org] = set()
        org_sources[org].add(src)

    sorted_orgs = sorted(org_counts.items(), key=lambda x: -x[1])[:top_n]

    print("\n=== TOP %d ОРГАНИЗАЦИЙ ПО КОЛИЧЕСТВУ ТЕНДЕРОВ ===" % top_n)
    print("%-50s %8s %s" % ("Организация", "Кол-во", "Площадки"))
    print("-" * 90)
    for org, count in sorted_orgs:
        sources = ", ".join(sorted(org_sources.get(org, set())))[:40]
        print("%-50s %8d %s" % (org[:50], count, sources))


def analyze_auction_discounts(client, top_n=20):
    """Analyze discounts from auction data (total_sum vs current bid)."""
    # Fetch auction tenders with price data
    data = _fetch_all(
        client, "tenders", "title, organization, price, source, external_id, search_text",
        filters=[
            lambda q: q.like("source", "%Birja%аукцион%"),
            lambda q: q.not_.is_("price", "null"),
            lambda q: q.gt("price", 0),
        ]
    )

    if not data:
        # Try alternative source name
        data = _fetch_all(
            client, "tenders", "title, organization, price, source, external_id",
            filters=[
                lambda q: q.like("source", "%birja%"),
                lambda q: q.not_.is_("price", "null"),
                lambda q: q.gt("price", 0),
            ]
        )

    if not data:
        print("\nNo auction data with prices found.")
        return

    # Group by organization
    org_data = {}  # type: dict
    for row in data:
        org = (row.get("organization") or "").strip()
        if not org:
            continue
        if org not in org_data:
            org_data[org] = {"count": 0, "total_value": 0}
        org_data[org]["count"] += 1
        price = float(row.get("price", 0) or 0)
        org_data[org]["total_value"] += price

    sorted_orgs = sorted(org_data.items(), key=lambda x: -x[1]["total_value"])[:top_n]

    print("\n=== TOP %d ОРГАНИЗАЦИЙ ПО СУММЕ АУКЦИОНОВ ===" % top_n)
    print("%-50s %8s %15s" % ("Организация", "Кол-во", "Сумма (UZS)"))
    print("-" * 80)
    for org, data in sorted_orgs:
        print("%-50s %8d %15s" % (
            org[:50],
            data["count"],
            "{:,.0f}".format(data["total_value"]),
        ))


def analyze_cooperation_stats(client):
    """Cooperation.uz statistics by category."""
    data = _fetch_all(
        client, "tenders", "source, title",
        filters=[lambda q: q.like("source", "Cooperation%")]
    )

    if not data:
        print("\nNo cooperation data found.")
        return

    # Count by source
    source_counts = {}  # type: dict
    for row in data:
        src = row.get("source", "")
        source_counts[src] = source_counts.get(src, 0) + 1

    print("\n=== COOPERATION.UZ ПО КАТЕГОРИЯМ ===")
    print("%-50s %8s" % ("Категория", "Записей"))
    print("-" * 60)
    for src, count in sorted(source_counts.items(), key=lambda x: -x[1]):
        print("%-50s %8d" % (src[:50], count))

    print("\nВсего: %d записей" % sum(source_counts.values()))


def overall_stats(client):
    """Overall tender database stats."""
    # Get exact total count without fetching all data
    total_count = _get_count(client, "tenders")

    # Fetch all rows for per-source breakdown
    data = _fetch_all(client, "tenders", "source")

    if not data:
        print("Empty database.")
        return

    source_counts = {}  # type: dict
    for row in data:
        src = row.get("source", "unknown")
        source_counts[src] = source_counts.get(src, 0) + 1

    print("\n=== ОБЩАЯ СТАТИСТИКА ТЕНДЕРОВ ===")
    print("Всего записей: %d" % total_count)
    print("\nПо площадкам:")
    print("%-50s %8s" % ("Площадка", "Записей"))
    print("-" * 60)
    for src, count in sorted(source_counts.items(), key=lambda x: -x[1]):
        print("%-50s %8d" % (src[:50], count))


def analyze_contracts(client, top_n=20):
    """Analyze ebirja contracts — winners and prices."""
    data = _fetch_all(
        client, "tenders", "title, organization, price, source, search_text, message_type",
        filters=[
            lambda q: q.eq("message_type", "contract"),
            lambda q: q.not_.is_("price", "null"),
            lambda q: q.gt("price", 0),
        ]
    )

    if not data:
        print("\nNo contract data found.")
        return

    print("\n=== ДОГОВОРЫ EBIRJA (ПОБЕДИТЕЛИ) ===")
    print("Всего договоров: %d" % len(data))

    # Group by buyer (organization)
    buyer_data = {}  # type: dict
    winner_data = {}  # type: dict
    for row in data:
        buyer = (row.get("organization") or "").strip()
        if buyer:
            if buyer not in buyer_data:
                buyer_data[buyer] = {"count": 0, "total": 0}
            buyer_data[buyer]["count"] += 1
            buyer_data[buyer]["total"] += float(row.get("price", 0) or 0)

        # Winner is in search_text (last part)
        search = row.get("search_text", "")
        parts = search.split()
        # search_text = "buyer winner lot contract" — winner is between buyer and lot
        # For now just count by source
        src = row.get("source", "")
        if src not in winner_data:
            winner_data[src] = 0
        winner_data[src] += 1

    sorted_buyers = sorted(buyer_data.items(), key=lambda x: -x[1]["total"])[:top_n]
    print("\n--- Топ заказчики (по сумме договоров) ---")
    print("%-50s %8s %15s" % ("Заказчик", "Кол-во", "Сумма (UZS)"))
    print("-" * 80)
    for buyer, data in sorted_buyers:
        print("%-50s %8d %15s" % (
            buyer[:50], data["count"], "{:,.0f}".format(data["total"])
        ))

    print("\n--- По типу договора ---")
    for src, count in sorted(winner_data.items(), key=lambda x: -x[1]):
        print("  %-50s %d" % (src[:50], count))


def main():
    parser = argparse.ArgumentParser(description="Tender analytics")
    parser.add_argument("--top", type=int, default=20, help="Top N results")
    args = parser.parse_args()

    client = get_client()

    overall_stats(client)
    analyze_cooperation_stats(client)
    analyze_by_organization(client, top_n=args.top)
    analyze_auction_discounts(client, top_n=args.top)
    analyze_contracts(client, top_n=args.top)


if __name__ == "__main__":
    main()
