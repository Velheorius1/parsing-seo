#!/usr/bin/env python3
"""CLI для записи фидбека по тендерным алертам.

Usage:
    python3 /opt/parsing-seo/crawler/scripts/parsing_feedback_cli.py 42 ad
    python3 /opt/parsing-seo/crawler/scripts/parsing_feedback_cli.py 42 client
    python3 /opt/parsing-seo/crawler/scripts/parsing_feedback_cli.py 42 irrelevant

Labels: client, ad, irrelevant
"""

import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")


LABEL_MAP = {
    "client": "client",
    "ok": "client",
    "ad": "ad",
    "irrelevant": "irrelevant",
    "skip": "irrelevant",
}


def main():
    if len(sys.argv) < 3:
        print("Usage: parsing_feedback_cli.py <alert_seq> <label>")
        print("Labels: client, ad, irrelevant")
        sys.exit(1)

    try:
        alert_seq = int(sys.argv[1])
    except ValueError:
        print("Error: alert_seq must be a number, got '%s'" % sys.argv[1])
        sys.exit(1)

    raw_label = sys.argv[2].lower().strip()
    corrected = LABEL_MAP.get(raw_label)
    if not corrected:
        print("Error: unknown label '%s'. Use: client, ad, irrelevant" % raw_label)
        sys.exit(1)

    from crawler.config.settings import settings
    from supabase import create_client
    client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    # Fetch tender info
    tender_id = None
    source = None
    message_text = None
    original_label = "demand"
    try:
        r = (
            client.table("tenders")
            .select("external_id,source,title,message_type")
            .eq("alert_seq", alert_seq)
            .limit(1)
            .execute()
        )
        if r.data:
            tender_id = r.data[0]["external_id"]
            source = r.data[0]["source"]
            message_text = r.data[0]["title"]
            original_label = r.data[0].get("message_type", original_label)
            print("Tender: %s (%s)" % (message_text[:60] if message_text else "?", source or "?"))
        else:
            print("Warning: tender #%d not found in DB" % alert_seq)
    except Exception as exc:
        print("Warning: could not fetch tender: %s" % str(exc)[:80])

    # Insert feedback
    try:
        client.table("alert_feedback").insert({
            "alert_seq": alert_seq,
            "tender_id": tender_id,
            "original_label": original_label,
            "corrected_label": corrected,
            "message_text": message_text,
            "source": source,
        }).execute()
        print("OK: #%03d -> %s" % (alert_seq, corrected))
    except Exception as exc:
        print("ERROR: %s" % str(exc)[:120])
        sys.exit(1)


if __name__ == "__main__":
    main()
