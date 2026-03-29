#!/usr/bin/env python3
"""Refine patterns — weekly analysis of user feedback corrections.

Reads alert_feedback + message_log from Supabase, finds:
- False positives: demand → ad/irrelevant (need to add to _AD_FILTER)
- False negatives: skipped → client (need to add to _DEMAND_PATTERNS)
- Common words in misclassified messages

Usage:
    python3 -m crawler.scripts.refine_patterns          # last 7 days
    python3 -m crawler.scripts.refine_patterns --days 30  # last 30 days
    python3 -m crawler.scripts.refine_patterns --send     # send report to Telegram

Deploy as weekly cron:
    0 9 * * 1 cd /opt/parsing-seo && .venv/bin/python -m crawler.scripts.refine_patterns --send
"""

import argparse
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from crawler.config.settings import settings
from crawler.core.db import _get_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("refine_patterns")

# Stop words to exclude from n-gram analysis (Ru + Uz)
STOP_WORDS = {
    "и", "в", "на", "с", "по", "за", "от", "до", "из", "не", "что", "это",
    "для", "как", "но", "или", "а", "о", "у", "к", "при", "все", "так",
    "бы", "мы", "вы", "он", "она", "они", "его", "её", "их",
    "va", "bu", "bir", "uchun", "bilan", "dan", "ga", "da", "ham", "yo",
    "the", "and", "for", "with", "from", "that", "this", "are", "was",
}


def fetch_feedback(days):
    # type: (int) -> List[dict]
    """Fetch feedback corrections from alert_feedback table."""
    client = _get_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    result = (
        client.table("alert_feedback")
        .select("alert_seq,original_label,corrected_label,message_text,source,created_at")
        .gte("created_at", cutoff)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data or []


def fetch_shadow_log(days):
    # type: (int) -> List[dict]
    """Fetch shadow-logged messages (for false negative analysis)."""
    client = _get_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    result = (
        client.table("message_log")
        .select("source,message_id,text,auto_label,created_at")
        .gte("created_at", cutoff)
        .order("created_at", desc=True)
        .limit(500)
        .execute()
    )
    return result.data or []


def tokenize(text):
    # type: (str) -> List[str]
    """Split text into lowercase tokens, removing stop words."""
    words = re.findall(r"[a-zA-Zа-яА-ЯёЁўғқҳ']+", text.lower())
    return [w for w in words if w not in STOP_WORDS and len(w) > 2]


def get_bigrams(tokens):
    # type: (List[str]) -> List[str]
    """Get bigrams from token list."""
    return ["%s %s" % (tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)]


def analyze_corrections(feedback):
    # type: (List[dict]) -> dict
    """Analyze feedback corrections to find patterns."""
    false_positives = []  # demand/customer_request → ad/irrelevant
    false_negatives = []  # ad/skipped → client
    confirmed = []  # correct classifications

    for row in feedback:
        orig = row.get("original_label", "")
        corr = row.get("corrected_label", "")
        text = row.get("message_text", "") or ""

        if orig in ("demand", "customer_request") and corr in ("ad", "irrelevant"):
            false_positives.append(text)
        elif orig in ("ad", "skipped") and corr == "client":
            false_negatives.append(text)
        else:
            confirmed.append(text)

    return {
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "confirmed": confirmed,
    }


def find_common_patterns(texts, top_n=15):
    # type: (List[str], int) -> Tuple[List[Tuple[str, int]], List[Tuple[str, int]]]
    """Find most common unigrams and bigrams in misclassified texts."""
    unigram_counter = Counter()  # type: Counter
    bigram_counter = Counter()  # type: Counter

    for text in texts:
        tokens = tokenize(text)
        unigram_counter.update(set(tokens))  # unique per message
        bigram_counter.update(set(get_bigrams(tokens)))

    return unigram_counter.most_common(top_n), bigram_counter.most_common(top_n)


def check_existing_filters(words):
    # type: (List[str]) -> List[str]
    """Check which words are NOT already in _AD_FILTER or _DEMAND_PATTERNS."""
    from crawler.adapters.telegram_adapter import _AD_FILTER, _DEMAND_PATTERNS

    ad_pattern_str = _AD_FILTER.pattern.lower()
    demand_pattern_str = _DEMAND_PATTERNS.pattern.lower()

    missing = []
    for word in words:
        w = word.lower()
        if w not in ad_pattern_str and w not in demand_pattern_str:
            missing.append(word)
    return missing


def generate_report(feedback, shadow_log, days):
    # type: (List[dict], List[dict], int) -> str
    """Generate human-readable analysis report."""
    analysis = analyze_corrections(feedback)
    fp_count = len(analysis["false_positives"])
    fn_count = len(analysis["false_negatives"])
    confirmed_count = len(analysis["confirmed"])
    total = fp_count + fn_count + confirmed_count

    lines = []
    lines.append("=== Refine Patterns Report ===")
    lines.append("Period: last %d days" % days)
    lines.append("Total feedback: %d" % total)
    lines.append("")

    if total == 0:
        lines.append("No feedback corrections found. Need more data.")
        return "\n".join(lines)

    accuracy = (confirmed_count / total * 100) if total else 0
    lines.append("Accuracy: %.1f%% (%d/%d correct)" % (accuracy, confirmed_count, total))
    lines.append("False positives (demand->ad): %d" % fp_count)
    lines.append("False negatives (skipped->client): %d" % fn_count)
    lines.append("")

    # False positives analysis
    if analysis["false_positives"]:
        lines.append("--- FALSE POSITIVES (classified demand, actually ad) ---")
        unigrams, bigrams = find_common_patterns(analysis["false_positives"])

        if unigrams:
            words = [w for w, _ in unigrams]
            missing = check_existing_filters(words)
            lines.append("Common words (not in filters): %s" % ", ".join(missing[:10]))
            lines.append("All common words: %s" % ", ".join("%s(%d)" % (w, c) for w, c in unigrams[:10]))

        if bigrams:
            lines.append("Common bigrams: %s" % ", ".join('"%s"(%d)' % (b, c) for b, c in bigrams[:8]))

        lines.append("")
        lines.append("Suggested _AD_FILTER additions:")
        if missing:
            for w in missing[:5]:
                lines.append("    r\"|%s\"" % re.escape(w))
        lines.append("")

        lines.append("Example false positives:")
        for text in analysis["false_positives"][:3]:
            lines.append("  - %s" % text[:120])
        lines.append("")

    # False negatives analysis
    if analysis["false_negatives"]:
        lines.append("--- FALSE NEGATIVES (classified ad/skip, actually client) ---")
        unigrams, bigrams = find_common_patterns(analysis["false_negatives"])

        if unigrams:
            words = [w for w, _ in unigrams]
            missing = check_existing_filters(words)
            lines.append("Common words (not in filters): %s" % ", ".join(missing[:10]))

        if bigrams:
            lines.append("Common bigrams: %s" % ", ".join('"%s"(%d)' % (b, c) for b, c in bigrams[:8]))

        lines.append("")
        lines.append("Suggested _DEMAND_PATTERNS additions:")
        if missing:
            for w in missing[:5]:
                lines.append("    r\"|%s\"" % re.escape(w))
        lines.append("")

        lines.append("Example false negatives:")
        for text in analysis["false_negatives"][:3]:
            lines.append("  - %s" % text[:120])
        lines.append("")

    # Shadow log stats
    if shadow_log:
        label_counts = Counter(row.get("auto_label", "?") for row in shadow_log)
        lines.append("--- Shadow Log (auto-labels, last %d days) ---" % days)
        for label, count in label_counts.most_common():
            lines.append("  %s: %d" % (label, count))
        lines.append("")

    return "\n".join(lines)


def send_report_telegram(report):
    # type: (str) -> bool
    """Send report to Telegram alert chat."""
    import httpx as _httpx

    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.error("Telegram credentials not set")
        return False

    # Truncate for Telegram limit
    text = report[:4000]
    if len(report) > 4000:
        text += "\n... (truncated)"

    try:
        resp = _httpx.post(
            "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
            json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": text,
                "disable_notification": True,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info("Report sent to Telegram")
            return True
        else:
            logger.warning("Telegram error: %d %s", resp.status_code, resp.text[:200])
            return False
    except Exception as exc:
        logger.error("Failed to send report: %s", str(exc))
        return False


def main():
    # type: () -> None
    parser = argparse.ArgumentParser(description="Analyze feedback corrections for pattern refinement")
    parser.add_argument("--days", type=int, default=7, help="Analysis period in days (default: 7)")
    parser.add_argument("--send", action="store_true", help="Send report to Telegram")
    args = parser.parse_args()

    logger.info("Analyzing feedback for last %d days...", args.days)

    feedback = fetch_feedback(args.days)
    shadow_log = fetch_shadow_log(args.days)

    report = generate_report(feedback, shadow_log, args.days)
    print(report)

    if args.send:
        send_report_telegram(report)


if __name__ == "__main__":
    main()
