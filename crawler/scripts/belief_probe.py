"""Перепроверка «аксиом» из crawler/config/beliefs.yaml (21.10.2026).

Зачем: «нельзя / мёртвый / закрыто» записывалось по одной пробе и жило без срока
годности — так старые аукционы UZEX три месяца считались «legacy», а список их
лотов — «закрытым токеном», хотя давно открылся (аудит 21.10). Здесь каждая
аксиома с пробой перепроверяется раз в неделю с VPS; итог — logs/beliefs_state.json,
его читает healthcheck (beliefs.<id>): переворот — FAIL «пересмотреть решение».

Пробы — по одному запросу на аксиому, последовательно, напрямую с VPS (без
прокси из окружения: аксиомы про гео-блок говорят именно о VPS); ответы не
сохраняются, только вывод «держится / не держится» и что увидели. «Не держится»
записывается только после второй пробы через паузу — один сетевой сбой не
должен поднимать тревогу «пересмотреть решение».

Cron (VPS): 40 5 * * 1  cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.belief_probe
Run: python3 -m crawler.scripts.belief_probe [--only ID] [--dry-run]
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import httpx
import yaml

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BELIEFS_PATH = os.path.join(_REPO, "crawler", "config", "beliefs.yaml")
STATE_PATH = os.path.join(_REPO, "logs", "beliefs_state.json")
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def load_beliefs(path=None):
    # type: (Optional[str]) -> list
    with open(path or BELIEFS_PATH, "r", encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("beliefs") or []


def _dig(data, path):
    # type: (Any, Optional[str]) -> Any
    for key in (path or "").split("."):
        if key and isinstance(data, dict):
            data = data.get(key)
    return data


def judge(expect, status, body, json_path=None):
    # type: (Dict[str, Any], Optional[int], Optional[str], Optional[str]) -> Tuple[bool, str]
    """(держится ли аксиома, что увидели). status=None — соединения нет."""
    if status is None:
        return bool(expect.get("unreachable")), "нет соединения"
    seen = "HTTP %d" % status
    if expect.get("unreachable"):
        return False, seen
    if "status_in" in expect:
        return status in expect["status_in"], seen
    if status != 200:
        return False, seen
    try:
        data = _dig(json.loads(body or ""), json_path)
    except ValueError:
        return False, "%s, не JSON" % seen
    if "json_list_min" in expect:
        n = len(data) if isinstance(data, list) else 0
        return n >= int(expect["json_list_min"]), "%s, список %d" % (seen, n)
    if "json_keys_absent" in expect:
        first = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None
        if first is None:
            return False, "%s, пустой список" % seen
        present = [k for k in expect["json_keys_absent"] if k in first]
        return not present, ("%s, есть поля: %s" % (seen, ", ".join(present))) if present else seen
    return True, seen


def probe(belief, client):
    # type: (Dict[str, Any], httpx.Client) -> Tuple[bool, str]
    p = belief["probe"]
    try:
        resp = client.request(p.get("method", "GET"), p["url"], json=p.get("json"),
                              headers={"User-Agent": _UA}, timeout=float(p.get("timeout", 20)))
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
        holds, seen = judge(p.get("expect") or {}, None, None)
        return holds, "%s (%s)" % (seen, type(exc).__name__)
    return judge(p.get("expect") or {}, resp.status_code, resp.text, p.get("json_path"))


def run(only=None, dry_run=False, beliefs_path=None, state_path=None, client=None, confirm_pause=30.0):
    # type: (Optional[str], bool, Optional[str], Optional[str], Optional[httpx.Client], float) -> Dict[str, Any]
    state_path = state_path or STATE_PATH
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    beliefs = [b for b in load_beliefs(beliefs_path) if b.get("probe")]
    state = {k: v for k, v in state.items() if k in {b["id"] for b in beliefs}}
    own = client is None
    client = client or httpx.Client(follow_redirects=True, trust_env=False)
    try:
        for b in beliefs:
            if only and b["id"] != only:
                continue
            holds, seen = probe(b, client)
            if not holds:
                time.sleep(confirm_pause)
                holds, again = probe(b, client)
                seen = "%s; повтор: %s" % (seen, again)
            state[b["id"]] = {"checked_at": datetime.now(timezone.utc).isoformat(),
                              "holds": holds, "observed": seen}
            print("%-32s %s  %s" % (b["id"], "держится" if holds else "НЕ ДЕРЖИТСЯ", seen))
    finally:
        if own:
            client.close()
    if not dry_run:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
    return state


def main():
    # type: () -> int
    ap = argparse.ArgumentParser(description="Перепроверка аксиом beliefs.yaml")
    ap.add_argument("--only")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    state = run(only=args.only, dry_run=args.dry_run)
    return 0 if all(v.get("holds") for v in state.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
