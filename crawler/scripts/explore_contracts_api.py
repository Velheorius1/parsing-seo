#!/usr/bin/env python3
"""Explore ebirja contracts API to discover available fields for winner analytics.

Usage: python -m crawler.scripts.explore_contracts_api
"""

import json
import httpx


def main():
    # Fetch first page of contracts
    url = "https://api.ebirja.uz/fond-api/api/external/contract/all"
    params = {"page": 0, "size": 3}  # just 3 records for exploration

    print("Fetching %s ..." % url)
    resp = httpx.get(url, params=params, timeout=15)
    data = resp.json()

    # Show top-level structure
    print("\n=== TOP-LEVEL KEYS ===")
    if isinstance(data, dict):
        for key in data.keys():
            val = data[key]
            if isinstance(val, dict):
                print("  %s: {%s}" % (key, ", ".join(val.keys())))
            elif isinstance(val, list):
                print("  %s: list[%d]" % (key, len(val)))
            else:
                print("  %s: %s" % (key, repr(val)[:80]))

    # Navigate to content
    content = data
    for path in ["data", "content"]:
        if isinstance(content, dict) and path in content:
            content = content[path]

    if isinstance(content, list) and len(content) > 0:
        print("\n=== RECORD FIELDS (first item) ===")
        first = content[0]
        for key, val in sorted(first.items()):
            print("  %-30s %s" % (key, repr(val)[:100]))

        print("\n=== ALL %d RECORDS (summary) ===" % len(content))
        for i, item in enumerate(content):
            print("  [%d] %s | buyer=%s | seller=%s | sum=%s" % (
                i,
                str(item.get("productName", ""))[:40],
                str(item.get("buyerName", ""))[:30],
                str(item.get("sellerName", ""))[:30],
                item.get("contractSum", "?"),
            ))

    # Also explore e-shop (reverse tenders) for comparison
    print("\n\n=== E-SHOP (reverse tenders) ===")
    eshop_url = "https://xarid-api.ebirja.uz/shop/product/announce-list"
    eshop_params = {"currentPage": 0, "perPage": 3, "platform_display": "e-shop"}
    resp2 = httpx.get(eshop_url, params=eshop_params, timeout=15)
    data2 = resp2.json()

    content2 = data2
    for path in ["result", "data"]:
        if isinstance(content2, dict) and path in content2:
            content2 = content2[path]

    if isinstance(content2, list) and len(content2) > 0:
        print("\n=== E-SHOP RECORD FIELDS (first item) ===")
        first2 = content2[0]
        for key, val in sorted(first2.items()):
            print("  %-30s %s" % (key, repr(val)[:100]))

    # Also explore active auctions for bid data
    print("\n\n=== ACTIVE AUCTIONS ===")
    auc_url = "https://xarid-api.ebirja.uz/auction/auction/active"
    auc_params = {"page": 0, "size": 3}
    resp3 = httpx.get(auc_url, params=auc_params, timeout=15)
    data3 = resp3.json()

    content3 = data3
    for path in ["result", "data"]:
        if isinstance(content3, dict) and path in content3:
            content3 = content3[path]

    if isinstance(content3, list) and len(content3) > 0:
        print("\n=== AUCTION RECORD FIELDS (first item) ===")
        first3 = content3[0]
        for key, val in sorted(first3.items()):
            print("  %-30s %s" % (key, repr(val)[:100]))


if __name__ == "__main__":
    main()
