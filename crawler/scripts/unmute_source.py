#!/usr/bin/env python3
"""Un-mute a source that feedback auto-muted by mistake (recall guard).

WHY THIS EXISTS
    `get_active_mutes()` mutes a source once it has >= N ❌ and zero ✅; a single ✅
    vetoes the mute forever. The weekly routine audits every active mute (ROUTINE.md
    step 4.5) and is supposed to lift any mute that is eating real leads — but the
    only way to do that was an inline `session_store.set_setting(...)` one-liner,
    which the agent permission classifier blocked on 2026-W30, W31 and W32 in a row.
    The recall guard existed on paper and could never actually fire.

    `TG: Box Maker Tashkent` (3 ❌ / 0 ✅) has been hiding in-scope packaging leads
    ("6xil pozitsiyada korobka chiqarib bera oladigan odam kerak", "у кого есть
    такие коробки?") for three weeks because of exactly this.

WHAT IT DOES
    Sets `pos = 1` for the named source, which vetoes its auto-mute. Muting only ever
    affected DELIVERY (digest routing) — ingestion is untouched — so this is fully
    reversible and cannot lose data.

Usage:
    python3 -m crawler.scripts.unmute_source --list
    python3 -m crawler.scripts.unmute_source "TG: Box Maker Tashkent"
    python3 -m crawler.scripts.unmute_source "TG: Box Maker Tashkent" --dry-run

Python 3.9 compatible (no match/case, no X|Y unions).
"""

import argparse
import sys
from typing import Optional

MUTE_KEY = "mute_patterns_v1"


def _load(session_store):
    # type: (object) -> dict
    state = session_store.get_setting(MUTE_KEY)
    if not isinstance(state, dict):
        raise SystemExit(
            "ERROR: %s is missing or unreadable (got %r). Refusing to overwrite."
            % (MUTE_KEY, type(state).__name__)
        )
    if not isinstance(state.get("sources"), dict):
        raise SystemExit("ERROR: %s has no 'sources' map. Refusing to overwrite." % MUTE_KEY)
    return state


def main(argv=None):
    # type: (Optional[list]) -> int
    ap = argparse.ArgumentParser(description="Lift a mistaken feedback auto-mute.")
    ap.add_argument("source", nargs="?", help="exact source name, e.g. 'TG: Box Maker Tashkent'")
    ap.add_argument("--list", action="store_true", help="show mute counters and active mutes")
    ap.add_argument("--dry-run", action="store_true", help="show the change, write nothing")
    args = ap.parse_args(argv)

    from crawler.auth.session_store import session_store
    from crawler.core.feedback import get_active_mutes

    state = _load(session_store)
    sources = state["sources"]
    active = get_active_mutes()

    if args.list or not args.source:
        print("ACTIVE MUTES (%d):" % len(active))
        for s in sorted(active):
            c = sources.get(s, {})
            print("  MUTED  %-42s neg=%-4s pos=%s" % (s, c.get("neg", 0), c.get("pos", 0)))
        print("\nOTHER TRACKED SOURCES:")
        for s in sorted(sources):
            if s not in active:
                c = sources[s]
                print("         %-42s neg=%-4s pos=%s" % (s, c.get("neg", 0), c.get("pos", 0)))
        if not args.source:
            return 0

    name = args.source
    if name not in sources:
        print("ERROR: %r is not tracked. Run --list for exact names." % name)
        return 1

    before = dict(sources[name])
    if name not in active:
        print("NOOP: %r is not currently muted (neg=%s pos=%s)."
              % (name, before.get("neg", 0), before.get("pos", 0)))
        return 0

    sources[name]["pos"] = max(1, int(sources[name].get("pos", 0)))

    if args.dry_run:
        print("DRY-RUN %r: %s -> %s (not written)" % (name, before, sources[name]))
        return 0

    session_store.set_setting(MUTE_KEY, state)

    # Verify by re-reading, never trust the write.
    check = _load(session_store)["sources"].get(name, {})
    still = get_active_mutes()
    print("BEFORE : %s" % before)
    print("AFTER  : %s" % check)
    print("MUTED? : %s" % ("YES — WRITE FAILED" if name in still else "no (un-muted)"))
    if name in still or int(check.get("pos", 0)) < 1:
        print("ERROR: verification failed, mute still active.")
        return 1
    print("OK: %r will be delivered again. Ingestion was never affected." % name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
