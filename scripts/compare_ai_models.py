#!/usr/bin/env python3
"""Compare AI relevance models from JSONL decision log.

Reads /var/log/parsing-seo-ai-decisions.jsonl, groups by model + role,
prints stats: accept rate, score distribution, latency, error rate.

Usage:
    python3 scripts/compare_ai_models.py                # last 7 days
    python3 scripts/compare_ai_models.py --days 1       # last 24h
    python3 scripts/compare_ai_models.py --since 2026-05-19
    python3 scripts/compare_ai_models.py --models qwen/qwen3-30b-a3b,deepseek/deepseek-v4-flash:free
    python3 scripts/compare_ai_models.py --role fast    # only fast role
    python3 scripts/compare_ai_models.py --examples 5   # show 5 example
                                                          decisions per model
"""

import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

LOG_PATH = os.environ.get(
    "PARSING_AI_LOG", "/var/log/parsing-seo-ai-decisions.jsonl"
)


def parse_iso(ts_str):
    # type: (str) -> Optional[datetime]
    try:
        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except Exception:
        return None


def load_decisions(path, since_dt, models_filter, role_filter):
    # type: (str, datetime, Optional[List[str]], Optional[str]) -> List[dict]
    decisions = []
    if not os.path.exists(path):
        print("[!] Log file not found: %s" % path, file=sys.stderr)
        return decisions
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            ts = parse_iso(row.get("ts", ""))
            if not ts or ts < since_dt:
                continue
            if models_filter and row.get("model") not in models_filter:
                continue
            if role_filter and row.get("role") != role_filter:
                continue
            decisions.append(row)
    return decisions


def pct(num, den):
    # type: (int, int) -> str
    if den <= 0:
        return "N/A"
    return "%.1f%%" % (100.0 * num / den)


def stats_for_group(rows):
    # type: (List[dict]) -> dict
    total = len(rows)
    errors = sum(1 for r in rows if r.get("error"))
    ok = total - errors
    accepted = sum(1 for r in rows if r.get("is_relevant") is True)
    rejected = sum(
        1 for r in rows if r.get("is_relevant") is False and not r.get("error")
    )
    scores = [r["score"] for r in rows if isinstance(r.get("score"), int)]
    latencies = [
        r["latency_ms"] for r in rows if isinstance(r.get("latency_ms"), int)
    ]
    http_429 = sum(1 for r in rows if r.get("http_status") == 429)
    http_500_plus = sum(
        1 for r in rows if isinstance(r.get("http_status"), int) and r["http_status"] >= 500
    )
    cat_counts = defaultdict(int)  # type: Dict[str, int]
    for r in rows:
        cat = r.get("category")
        if cat:
            cat_counts[cat] += 1

    return {
        "total": total,
        "ok": ok,
        "errors": errors,
        "accepted": accepted,
        "rejected": rejected,
        "scores": scores,
        "latencies": latencies,
        "http_429": http_429,
        "http_5xx": http_500_plus,
        "categories": dict(cat_counts),
    }


def fmt_p(values, p):
    # type: (List[int], float) -> str
    if not values:
        return "N/A"
    try:
        sorted_vals = sorted(values)
        idx = int(len(sorted_vals) * p / 100)
        idx = max(0, min(len(sorted_vals) - 1, idx))
        return "%dms" % sorted_vals[idx]
    except Exception:
        return "N/A"


def print_group(model, role, s):
    # type: (str, str, dict) -> None
    print("")
    print("━━━ %s (role=%s) ━━━" % (model, role))
    print("  Total calls       : %d" % s["total"])
    print("  Errors            : %d (%s)" % (s["errors"], pct(s["errors"], s["total"])))
    print("    └ HTTP 429      : %d" % s["http_429"])
    print("    └ HTTP 5xx      : %d" % s["http_5xx"])
    if s["ok"] > 0:
        print(
            "  Accepted (relev.) : %d (%s of ok)"
            % (s["accepted"], pct(s["accepted"], s["ok"]))
        )
        print(
            "  Rejected          : %d (%s of ok)"
            % (s["rejected"], pct(s["rejected"], s["ok"]))
        )
    if s["scores"]:
        print(
            "  Score: mean=%.1f  median=%d  min=%d  max=%d"
            % (
                statistics.mean(s["scores"]),
                statistics.median(s["scores"]),
                min(s["scores"]),
                max(s["scores"]),
            )
        )
    if s["latencies"]:
        print(
            "  Latency: p50=%s  p95=%s  max=%dms"
            % (
                fmt_p(s["latencies"], 50),
                fmt_p(s["latencies"], 95),
                max(s["latencies"]),
            )
        )
    if s["categories"]:
        cats = ", ".join("%s=%d" % (k, v) for k, v in sorted(s["categories"].items()))
        print("  Categories        : %s" % cats)


def print_examples(rows, n, model, role):
    # type: (List[dict], int, str, str) -> None
    if n <= 0:
        return
    examples = [r for r in rows if r.get("model") == model and r.get("role") == role]
    if not examples:
        return
    print("")
    print("  Examples (first %d):" % n)
    for r in examples[:n]:
        score = r.get("score")
        score_str = "%d" % score if isinstance(score, int) else "—"
        cat = r.get("category") or "—"
        title = (r.get("title") or "")[:80]
        err = r.get("error")
        flag = "✓" if r.get("is_relevant") else ("✗" if r.get("is_relevant") is False else "?")
        if err:
            print("    [ERR %s] %s — %s" % (flag, title, err[:60]))
        else:
            print("    [%s s=%s cat=%s] %s" % (flag, score_str, cat, title))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="Look back N days (default 7)")
    parser.add_argument("--since", type=str, help="Look back since YYYY-MM-DD (UTC)")
    parser.add_argument("--models", type=str, help="Comma-separated model filter")
    parser.add_argument("--role", type=str, help="Role filter: fast | max")
    parser.add_argument("--examples", type=int, default=0, help="Show N example decisions per group")
    parser.add_argument("--log", type=str, default=LOG_PATH, help="JSONL log path")
    args = parser.parse_args()

    if args.since:
        try:
            since_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except Exception:
            print("[!] Bad --since format, expected YYYY-MM-DD", file=sys.stderr)
            return 1
    else:
        since_dt = datetime.now(timezone.utc) - timedelta(days=args.days)

    models_filter = None
    if args.models:
        models_filter = [m.strip() for m in args.models.split(",") if m.strip()]

    rows = load_decisions(args.log, since_dt, models_filter, args.role)
    if not rows:
        print("[!] No decisions found in window (since %s)." % since_dt.strftime("%Y-%m-%d %H:%M"))
        return 0

    print("══════════════════════════════════════════")
    print("AI Model Comparison — %s" % args.log)
    print("Window: %s → now (%d rows)" % (since_dt.strftime("%Y-%m-%d %H:%M UTC"), len(rows)))
    if models_filter:
        print("Models: %s" % ", ".join(models_filter))
    if args.role:
        print("Role:   %s" % args.role)
    print("══════════════════════════════════════════")

    # Group by (model, role)
    groups = defaultdict(list)  # type: Dict[tuple, List[dict]]
    for r in rows:
        key = (r.get("model", "?"), r.get("role", "?"))
        groups[key].append(r)

    for (model, role), group_rows in sorted(groups.items()):
        s = stats_for_group(group_rows)
        print_group(model, role, s)
        if args.examples > 0:
            print_examples(group_rows, args.examples, model, role)

    # Cross-model summary (same role)
    role_groups = defaultdict(dict)  # type: Dict[str, Dict[str, dict]]
    for (model, role), group_rows in groups.items():
        role_groups[role][model] = stats_for_group(group_rows)

    print("")
    print("══════ Summary (same role) ══════")
    for role, model_stats in sorted(role_groups.items()):
        if len(model_stats) < 2:
            continue
        print("")
        print("Role: %s" % role)
        print(
            "  %-50s %8s %8s %8s %8s"
            % ("model", "calls", "accept%", "err%", "p50ms")
        )
        for model, s in sorted(model_stats.items()):
            acc = pct(s["accepted"], s["ok"]) if s["ok"] else "N/A"
            err = pct(s["errors"], s["total"])
            p50 = fmt_p(s["latencies"], 50)
            print(
                "  %-50s %8d %8s %8s %8s"
                % (model[:50], s["total"], acc, err, p50)
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
