#!/usr/bin/env python3
"""
Fetch all UZ trading platforms from TenderZone SBIS API.
Compare with our sources.yaml and output gap analysis.

Usage: python3 scripts/fetch_tzone_platforms.py
"""

import json
import sys
import os
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required. Install: pip install pyyaml")
    sys.exit(1)


TZONE_URL = "https://tzone.uz/service/?srv=1"
TZONE_HEADERS = {
    "Content-Type": "application/json; charset=utf-8;type=rpc",
    "X-Requested-With": "XMLHttpRequest",
}
TZONE_BODY = {
    "jsonrpc": "2.0",
    "protocol": 4,
    "method": "TradingPlatform.GetList",
    "params": {
        "\u0414\u043e\u043f\u041f\u043e\u043b\u044f": [],
        "\u0424\u0438\u043b\u044c\u0442\u0440": {
            "_type": "record",
            "d": [["860"], None],
            "s": [
                {"n": "country_code", "t": {"n": "\u041c\u0430\u0441\u0441\u0438\u0432", "t": "\u0421\u0442\u0440\u043e\u043a\u0430"}},
                {"n": "searchString", "t": "\u0421\u0442\u0440\u043e\u043a\u0430"},
            ],
            "f": 0,
        },
        "\u0421\u043e\u0440\u0442\u0438\u0440\u043e\u0432\u043a\u0430": None,
        "\u041d\u0430\u0432\u0438\u0433\u0430\u0446\u0438\u044f": {
            "_type": "record",
            "d": [0, 500, True],
            "s": [
                {"n": "\u0421\u0442\u0440\u0430\u043d\u0438\u0446\u0430", "t": "\u0427\u0438\u0441\u043b\u043e \u0446\u0435\u043b\u043e\u0435"},
                {"n": "\u0420\u0430\u0437\u043c\u0435\u0440\u0421\u0442\u0440\u0430\u043d\u0438\u0446\u044b", "t": "\u0427\u0438\u0441\u043b\u043e \u0446\u0435\u043b\u043e\u0435"},
                {"n": "\u0415\u0441\u0442\u044c\u0415\u0449\u0435", "t": "\u041b\u043e\u0433\u0438\u0447\u0435\u0441\u043a\u043e\u0435"},
            ],
            "f": 0,
        },
    },
    "id": 1,
}

SOURCES_YAML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "crawler", "config", "sources.yaml",
)

OUTPUT_MD = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "docs", "platform-gap-analysis.md",
)


def fetch_tzone_platforms():
    """Call TenderZone API and return list of platform dicts."""
    body = json.dumps(TZONE_BODY, ensure_ascii=False).encode("utf-8")
    req = Request(TZONE_URL, data=body, headers=TZONE_HEADERS, method="POST")

    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, HTTPError) as e:
        print("ERROR: Failed to call TZone API: {}".format(e))
        sys.exit(1)

    if "error" in data:
        print("ERROR: API returned error: {}".format(data["error"]))
        sys.exit(1)

    result = data.get("result", {})
    schema = result.get("s", [])
    rows = result.get("d", [])

    # Build field name list from schema
    field_names = [s["n"] for s in schema]
    print("Schema fields: {}".format(field_names))
    print("Total rows returned: {}".format(len(rows)))

    platforms = []
    for row_data in rows:
        # Each row is a flat list matching schema order
        row = row_data if isinstance(row_data, list) else row_data.get("d", [])
        platform = {}
        for i, field in enumerate(field_names):
            if i < len(row):
                platform[field] = row[i]
        platforms.append(platform)

    return platforms


def load_our_sources():
    """Load source IDs from sources.yaml."""
    with open(SOURCES_YAML, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    sources = cfg.get("sources", [])
    source_ids = set()
    source_names = {}
    source_urls = {}
    for s in sources:
        sid = s.get("id", "")
        source_ids.add(sid)
        source_names[sid] = s.get("name", "")
        source_urls[sid] = s.get("url", "")
    return source_ids, source_names, source_urls


def normalize_url(url):
    """Normalize URL for comparison."""
    if not url:
        return ""
    url = url.lower().strip().rstrip("/")
    url = url.replace("https://", "").replace("http://", "").replace("www.", "")
    return url


def match_platform_to_source(platform, source_ids, source_names, source_urls):
    """Try to match a TZone platform to one of our sources."""
    pname = (platform.get("name") or "").lower()
    purl = normalize_url(platform.get("url") or "")
    pbrief = (platform.get("brief") or "").lower()

    # Check URL match
    for sid, surl in source_urls.items():
        if not surl:
            continue
        nurl = normalize_url(surl)
        # Check if domains overlap
        purl_domain = purl.split("/")[0] if purl else ""
        surl_domain = nurl.split("/")[0] if nurl else ""
        if purl_domain and surl_domain and (
            purl_domain in surl_domain or surl_domain in purl_domain
        ):
            return sid

    # Check name similarity
    name_keywords = {
        "uzex": ["uzex", "etender", "xarid", "ebirja", "hayotbirja", "dxarid", "exarid"],
        "cooperation": ["cooperation"],
        "tashkent-steel": ["tashkent", "steel", "metallurgical"],
        "minstroy": ["minstroy", "qurilish"],
        "lukoil": ["lukoil"],
        "uzbekenergo": ["uzbekenergo", "energiya"],
        "uzkimyo": ["kimyo"],
        "railway": ["temir", "railway"],
        "mobiuz": ["mobiuz"],
        "ucell": ["ucell"],
        "navoi-gmk": ["navoi"],
        "agmk": ["agmk", "almalyk"],
    }
    for sid, keywords in name_keywords.items():
        for kw in keywords:
            if kw in pname or kw in pbrief:
                if sid in source_ids:
                    return sid

    return None


def main():
    print("=== TenderZone UZ Platform Gap Analysis ===\n")

    # 1. Fetch platforms from TZone
    print("Fetching platforms from TZone API...")
    platforms = fetch_tzone_platforms()

    # Filter: individual platforms (not groups) with country=UZ
    individual = []
    for p in platforms:
        is_group = p.get("type@", False)
        country = p.get("country_iso_code", "")
        if not is_group and country == "UZ":
            individual.append(p)

    print("\nIndividual UZ platforms: {}".format(len(individual)))
    # Also show non-UZ stats
    all_individual = [p for p in platforms if not p.get("type@", False)]
    print("Total individual platforms (all countries): {}".format(len(all_individual)))

    # 2. Load our sources
    print("\nLoading our sources.yaml...")
    source_ids, source_names, source_urls = load_our_sources()
    print("Our sources: {} total".format(len(source_ids)))

    # 3. Compare
    matched = []
    unmatched = []

    for p in individual:
        name = p.get("name", "N/A")
        url = p.get("url", "N/A")
        meter = p.get("meter", 0)
        brief = p.get("brief", "")
        tp_id = p.get("tp", "")

        our_sid = match_platform_to_source(p, source_ids, source_names, source_urls)
        entry = {
            "tzone_name": name,
            "tzone_url": url,
            "tzone_brief": brief,
            "tzone_id": tp_id,
            "tender_count": meter,
            "our_source": our_sid,
        }
        if our_sid:
            matched.append(entry)
        else:
            unmatched.append(entry)

    # Sort unmatched by tender count desc
    unmatched.sort(key=lambda x: x.get("tender_count", 0) or 0, reverse=True)

    # 4. Print results
    print("\n--- MATCHED ({}) ---".format(len(matched)))
    for m in matched:
        print("  [OK] {} -> {} ({} tenders)".format(
            m["tzone_name"], m["our_source"], m["tender_count"]))

    print("\n--- MISSING ({}) ---".format(len(unmatched)))
    for u in unmatched:
        print("  [GAP] {} | {} | {} tenders".format(
            u["tzone_name"], u["tzone_url"], u["tender_count"]))

    # 5. Save to markdown
    os.makedirs(os.path.dirname(OUTPUT_MD), exist_ok=True)
    with open(OUTPUT_MD, "w", encoding="utf-8") as f:
        f.write("# Platform Gap Analysis: TenderZone vs Our Sources\n\n")
        f.write("**Date:** 2026-03-15\n")
        f.write("**Source:** TenderZone SBIS API (TradingPlatform.GetList, country=UZ)\n\n")

        f.write("## Summary\n\n")
        f.write("| Metric | Count |\n")
        f.write("|--------|-------|\n")
        f.write("| TZone UZ platforms | {} |\n".format(len(individual)))
        f.write("| Matched to our sources | {} |\n".format(len(matched)))
        f.write("| Missing (gap) | {} |\n\n".format(len(unmatched)))

        f.write("## Matched Platforms\n\n")
        f.write("| TZone Platform | Our Source ID | Tenders |\n")
        f.write("|---------------|-------------|--------|\n")
        for m in matched:
            f.write("| {} | `{}` | {:,} |\n".format(
                m["tzone_name"], m["our_source"], m["tender_count"] or 0))

        f.write("\n## Missing Platforms (Gap)\n\n")
        f.write("| # | TZone Platform | URL | Tenders | Priority |\n")
        f.write("|---|---------------|-----|---------|----------|\n")
        for i, u in enumerate(unmatched, 1):
            count = u["tender_count"] or 0
            priority = "HIGH" if count > 1000 else ("MEDIUM" if count > 100 else "LOW")
            f.write("| {} | {} | {} | {:,} | {} |\n".format(
                i, u["tzone_name"], u["tzone_url"], count, priority))

        f.write("\n## All TZone UZ Platforms (raw)\n\n")
        f.write("| ID | Name | Brief | URL | Tenders |\n")
        f.write("|----|------|-------|-----|--------|\n")
        for p in individual:
            f.write("| {} | {} | {} | {} | {:,} |\n".format(
                p.get("tp", ""),
                p.get("name", ""),
                p.get("brief", ""),
                p.get("url", ""),
                p.get("meter", 0) or 0,
            ))

    print("\nGap analysis saved to: {}".format(OUTPUT_MD))


if __name__ == "__main__":
    main()
