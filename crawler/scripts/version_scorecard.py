"""version_scorecard — 0-10 балл КАЖДОЙ версии краулера на замороженном корпусе.

Зачем (Данияр 27.07): «в погоне за улучшениями не сделать его тупее». За июль
пайплайн менялся десятки раз, и ни одна правка не проверялась против эталона —
регрессия recall'а обнаруживалась случайно. Это тот эталон.

Чем ОТЛИЧАЕТСЯ от weekly_metrics.py (не дублирует, ортогонален):
    weekly_metrics  — операционный балл ЖИВОЙ недели. Данные меняются неделя к
                      неделе, поэтому баллы разных версий кода несравнимы.
    version_scorecard — детерминированный балл ВЕРСИИ КОДА на одном и том же
                      наборе. Сравним между коммитами, несравним между
                      corpus_version (смена корпуса = осознанный rebaseline).

Режимы:
  --log-version   тик HEAD при смене (бесплатно, daily cron)
  --fast          только префильтр, 0 сети, exit 1 при регрессии
  --score [--tg]  полный прогон (префильтр + AI + роутинг) → компоненты → балл

Где что запускается: `--fast` и `--score` требуют прод-зависимостей
(pydantic_settings и т.д.), которых на Mac нет — гоняются на VPS. Локальный
гейт перед push — это `python3 -m crawler.tests.test_version_scorecard`: он
стабит настройки и проверяет и арифметику, и схему корпуса.

Метрика — на expect_*, а не на label: label говорит «настоящий ли это заказ»,
expect_delivered — «что политика предписывает сделать сегодня». Дешёвый, но
настоящий лот = relevant + expect_delivered false. Поэтому смена порога цены
видна как rebaseline, а не как тихая просадка.

Cron (host):
  15 6 * * *  ... version_scorecard --log-version
  20 6 * * 1  ... version_scorecard --score --tg
"""
import os

# Before ANY crawler import: ai_decision_log resolves its path at import time,
# and benchmark traffic must not pollute the prod model-comparison log.
os.environ.setdefault("PARSING_AI_LOG", "/tmp/benchmark-ai-decisions.jsonl")

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, "/opt/parsing-seo")

_REPO = "/opt/parsing-seo" if os.path.isdir("/opt/parsing-seo/crawler") else \
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CORPUS = os.path.join(_REPO, "crawler", "benchmark", "corpus_v1.json")
LOG_DIR = os.environ.get("METRICS_DIR") or os.path.join(_REPO, "logs")
LOG_PATH = os.path.join(LOG_DIR, "version_scores.jsonl")

WEIGHTS = {"recall": 0.40, "precision": 0.30, "routing": 0.15, "prefilter": 0.15}
AI_ERROR_DEGRADED = 0.15   # above this the run is not comparable
RED_FLAG_DROP = 1.0        # points; below this is provider noise, not regression


def _git(*args):
    try:
        return subprocess.check_output(("git",) + args, cwd=_REPO,
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return ""


def _load_corpus():
    c = json.load(open(CORPUS, encoding="utf-8"))
    live = [e for e in c["entries"] if not e.get("retired")]
    return c["corpus_version"], live


def _to_tender(e):
    from crawler.core.models import RawTender
    return RawTender(
        id=e["external_id"], external_id=e["external_id"],
        title=e.get("title") or "", organization=e.get("organization") or "",
        price=e.get("price"), currency=e.get("currency") or "UZS",
        deadline=e.get("deadline"), source=e.get("source") or "",
        status=e.get("status") or "active", search_text=e.get("search_text") or "",
        message_type=e.get("message_type") or "tender",
        bid_count=e.get("bid_count"),
        extra_info=dict((str(k), str(v)) for k, v in (e.get("extra_info") or {}).items()),
    )


def _fingerprint():
    """The score depends on more than git: keywords live in .env, playbook and
    tnved_scope in the DB. Without this a config drift reads as a code regression."""
    from crawler.core.notifier import _get_keywords, _load_tnved_scope
    from crawler.config.settings import settings
    kws = ",".join(sorted(_get_keywords()))
    try:
        from crawler.core.feedback import get_relevance_playbook
        pb = get_relevance_playbook()
    except Exception:
        pb = ""
    return {
        "keywords_sha1": hashlib.sha1(kws.encode()).hexdigest()[:12],
        "keywords_n": len(kws.split(",")) if kws else 0,
        "playbook_sha1": hashlib.sha1(pb.encode()).hexdigest()[:12],
        "playbook_lines": len([x for x in pb.split("\n") if x.strip()]),
        "tnved_scope": ",".join(_load_tnved_scope()),
        "model_fast": settings.ai_relevance_model_fast,
        "model_max": settings.ai_relevance_model,
        "threshold": settings.ai_score_threshold,
    }


async def _run_corpus(entries, use_ai):
    """Replay every entry as of its own frozen_now. Returns [(entry, verdict)]."""
    from crawler.scripts.replay import replay_tenders

    tenders = [_to_tender(e) for e in entries]
    frozen = dict((e["external_id"], e.get("frozen_now")) for e in entries)
    verdicts = await replay_tenders(tenders, use_ai=use_ai, as_of="collected_at",
                                    mutes=set(), collected_at=frozen)
    return list(zip(entries, verdicts))


def _score(pairs):
    """Components over expect_*, with transport failures excluded from the
    denominators (a dead OpenRouter is not a dumber crawler)."""
    scored = [(e, v) for e, v in pairs if not v.ai_error]
    n_err = len(pairs) - len(scored)

    want_del = [(e, v) for e, v in scored if e.get("expect_delivered")]
    want_drop = [(e, v) for e, v in scored if not e.get("expect_delivered")]
    recall = (len([1 for _e, v in want_del if v.delivered]) / float(len(want_del))
              if want_del else 1.0)
    precision = (len([1 for _e, v in want_drop if not v.delivered]) / float(len(want_drop))
                 if want_drop else 1.0)

    routed = [(e, v) for e, v in scored
              if v.delivered and e.get("expect_route") in ("push", "digest")]
    routing = (len([1 for e, v in routed if v.route == e["expect_route"]]) / float(len(routed))
               if routed else 1.0)

    pipe = [(e, v) for e, v in scored
            if e.get("kind") == "pipeline" and e.get("expect_delivered")]
    prefilter = (len([1 for _e, v in pipe if v.passed_prefilter]) / float(len(pipe))
                 if pipe else 1.0)

    comps = {"recall": round(recall, 4), "precision": round(precision, 4),
             "routing": round(routing, 4), "prefilter": round(prefilter, 4)}
    total = sum(WEIGHTS[k] * comps[k] for k in WEIGHTS)
    misses = []
    for e, v in scored:
        if e.get("expect_delivered") and not v.delivered:
            misses.append({"cid": e["cid"], "stage": v.dropped_at_stage or
                           ("ai:%s" % v.ai_score)})
        elif not e.get("expect_delivered") and v.delivered:
            misses.append({"cid": e["cid"], "stage": "false-positive"})
    return {
        "components": comps,
        "score": round(10.0 * total, 1),
        "n_entries": len(pairs),
        "n_scored": len(scored),
        "ai_error_rate": round(n_err / float(len(pairs)), 4) if pairs else 0.0,
        "fail_open_delivered": len([1 for _e, v in pairs if v.ai_error and v.delivered]),
        "misses": misses[:32],
    }


def _history():
    if not os.path.exists(LOG_PATH):
        return []
    out = []
    for line in open(LOG_PATH, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _append(rec):
    if not os.path.isdir(LOG_DIR):
        os.makedirs(LOG_DIR)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def log_version():
    """Cheap daily tick — records which commit prod is running, so a later score
    change can be attributed to a deploy instead of guessed at."""
    sha = _git("rev-parse", "--short", "HEAD")
    if not sha:
        print("not a git checkout — skip")
        return
    prev = [r for r in _history() if r.get("kind") == "version"]
    if prev and prev[-1].get("git_sha") == sha:
        print("HEAD unchanged (%s) — no tick" % sha)
        return
    rec = {"kind": "version", "ts": datetime.now(timezone.utc).isoformat(),
           "git_sha": sha, "git_subject": _git("log", "-1", "--format=%s")[:120]}
    _append(rec)
    print(">>> version tick:", sha, rec["git_subject"][:60])


def _fmt_tg(rec, prev):
    L = ["\U0001f4d0 *Балл версии краулера*  —  *%s / 10*" % rec["score"]]
    if rec.get("degraded"):
        L.append("⚠️ AI нестабилен (%d%% ошибок) — прогон НЕ сравним, дельту не считаю."
                 % round(100 * rec["ai_error_rate"]))
    elif prev:
        d = round(rec["score"] - prev["score"], 1)
        if rec["corpus_version"] != prev.get("corpus_version"):
            L.append("_rebaseline %s → %s: с прошлым баллом не сравнивается._"
                     % (prev.get("corpus_version"), rec["corpus_version"]))
        else:
            arrow = "🟥" if d <= -RED_FLAG_DROP else ("▲" if d > 0 else ("▼" if d < 0 else "="))
            L.append("%s %+.1f к прошлому прогону (%s)" % (arrow, d, prev.get("git_sha", "?")[:7]))
            if d <= -RED_FLAG_DROP:
                same_code = rec["git_sha"] == prev.get("git_sha")
                same_cfg = rec["config_fingerprint"] == prev.get("config_fingerprint")
                if same_code and not same_cfg:
                    L.append("Код не менялся — сместился *конфиг* (playbook/ключевики).")
                elif same_code and same_cfg:
                    L.append("Код и конфиг те же — похоже на *шум провайдера*, перепроверь.")
                else:
                    L.append("*Проверить последний деплой.*")
    L.append("")
    L.append("```")
    c = rec["components"]
    L.append("%-12s %5.0f%%  (вес %.2f)" % ("recall", 100 * c["recall"], WEIGHTS["recall"]))
    L.append("%-12s %5.0f%%  (вес %.2f)" % ("precision", 100 * c["precision"], WEIGHTS["precision"]))
    L.append("%-12s %5.0f%%  (вес %.2f)" % ("routing", 100 * c["routing"], WEIGHTS["routing"]))
    L.append("%-12s %5.0f%%  (вес %.2f)" % ("prefilter", 100 * c["prefilter"], WEIGHTS["prefilter"]))
    L.append("")
    L.append("корпус %s: %d записей, оценено %d" % (
        rec["corpus_version"], rec["n_entries"], rec["n_scored"]))
    L.append("коммит %s" % rec["git_sha"])
    L.append("```")
    if rec["misses"]:
        top = Counter(m["stage"] for m in rec["misses"]).most_common(4)
        L.append("*Промахи:* " + ", ".join("%s×%d" % (s, n) for s, n in top))
    return "\n".join(L)


def score(send_tg):
    version, entries = _load_corpus()
    print("corpus %s: %d live entries" % (version, len(entries)))
    pairs = asyncio.run(_run_corpus(entries, use_ai=True))
    rec = _score(pairs)
    rec.update({
        "kind": "score", "ts": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git("rev-parse", "--short", "HEAD"),
        "git_subject": _git("log", "-1", "--format=%s")[:120],
        "corpus_version": version,
        "config_fingerprint": _fingerprint(),
    })
    rec["degraded"] = rec["ai_error_rate"] > AI_ERROR_DEGRADED
    prev_scores = [r for r in _history() if r.get("kind") == "score"]
    rec["baseline"] = not prev_scores
    _append(rec)

    print(json.dumps({k: rec[k] for k in
                      ("score", "components", "n_scored", "ai_error_rate", "degraded")},
                     ensure_ascii=False, indent=1))
    text = _fmt_tg(rec, prev_scores[-1] if prev_scores else None)
    print("\n" + text)
    if send_tg:
        import httpx
        from crawler.config.settings import settings
        try:
            with httpx.Client(timeout=20, trust_env=False) as c:
                r = c.post("https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                           json={"chat_id": settings.telegram_alert_chat_id, "text": text,
                                 "parse_mode": "Markdown", "disable_web_page_preview": True})
            print("[TG]", r.status_code)
        except Exception as exc:
            print("[TG] failed:", str(exc)[:120])
    return rec


def fast():
    """Prefilter-only gate: free, offline, deterministic. Non-zero exit if any
    entry that MUST reach the pipeline dies in the deterministic stages."""
    version, entries = _load_corpus()
    pairs = asyncio.run(_run_corpus(entries, use_ai=False))
    broken = [(e, v) for e, v in pairs
              if e.get("kind") == "pipeline" and e.get("expect_delivered")
              and not v.passed_prefilter]
    print("corpus %s: %d entries, prefilter-critical %d, broken %d" % (
        version, len(entries),
        len([e for e, _ in pairs if e.get("kind") == "pipeline" and e.get("expect_delivered")]),
        len(broken)))
    for e, v in broken:
        print("  %s  %-18s  %s" % (e["cid"], v.dropped_at_stage, (e.get("title") or "")[:52]))
    return 1 if broken else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log-version", action="store_true")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--tg", action="store_true")
    a = ap.parse_args()
    if a.log_version:
        log_version()
    elif a.fast:
        sys.exit(fast())
    elif a.score:
        score(a.tg)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
