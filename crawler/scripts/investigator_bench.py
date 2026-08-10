"""investigator_bench — мерило для РАЗБОРЩИКА лотов.

Зачем. Версионный скоркарт (version_scorecard.py) меряет гейт релевантности —
пускать лот к человеку или нет. Разбор лота (investigator.py) — отдельная машина:
она ходит по площадке, читает документы и выносит вердикт «участвовать /
пропустить / уточнить». Её не мерило НИЧТО. Ошибку 06.08 на лоте за 1,23 млрд
(картхолдер сочли кожевенным производством, потому что так была заполнена
категория площадки) поймал Данияр глазами — другого способа не существовало.

Как считаем. Разметка — клики Данияра (alert_feedback.corrected_label) плюс один
лот, разобранный вручную по документам. Клик отвечает на вопрос «наш ли это тип
заказа», вердикт — на вопрос «стоит ли участвовать», и второй ШИРЕ первого.
Поэтому считаются только ГРУБЫЕ промахи, где расхождение не спишешь на широту:

    ждали «участвовать», получили «пропустить»   -> грубый промах (упустили своё)
    ждали «пропустить»,  получили «участвовать»  -> грубый промах (позвали в чужое)
    «уточнить»                                    -> не промах, но считается отдельно

«Уточнить» не штрафуется: при нехватке данных это честный ответ, и промпт прямо
требует его вместо «пропустить». Но доля уточнений выводится рядом с точностью —
разборщик, уточняющий всегда, формально безошибочен и практически бесполезен.

Отдельно считаются СБОИ (вердикта нет вовсе: оборвалась сеть, кончились ходы,
страница лота умерла). Они не идут в знаменатель точности — иначе гниение корпуса
читалось бы как деградация разборщика, — но выводятся явно.

Стоимость. Каждая запись — полный агентный прогон с инструментами и OCR, порядка
$0.02-0.15. Двенадцать записей ~ $1. Крона нет и не планируется: запускать руками
перед и после правок разборщика.

Usage:
  python3 -m crawler.scripts.investigator_bench [--limit N] [--cid inv-001] [--send]
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/opt/parsing-seo")

from crawler.config.settings import settings

_CORPUS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "benchmark", "investigator_corpus_v1.json")
_LOG = "/opt/parsing-seo/logs/investigator_bench.jsonl"

HEDGE = "уточнить"


def load_corpus(path=None):
    with open(path or _CORPUS, encoding="utf-8") as f:
        data = json.load(f)
    return [e for e in data.get("entries", []) if not e.get("retired")], data


def classify(expect, got):
    # type: (str, str) -> str
    """Одна запись -> исход. Чистая функция, вся арифметика отчёта стоит на ней."""
    if not got:
        return "сбой"
    if got == HEDGE:
        return "уточнение"
    return "верно" if got == expect else "грубый промах"


def score(results):
    # type: (list) -> dict
    """results: список dict с ключами outcome/expect/got/cid.

    Точность считается по РЕШИТЕЛЬНЫМ ответам (верно + грубый промах). Сбои и
    уточнения из знаменателя исключены, но их доли выводятся: без них цифра
    точности врёт в обе стороны."""
    out = {"n": len(results), "верно": 0, "грубый промах": 0, "уточнение": 0, "сбой": 0,
           "misses": []}
    for r in results:
        out[r["outcome"]] = out.get(r["outcome"], 0) + 1
        if r["outcome"] == "грубый промах":
            out["misses"].append(r)
    decisive = out["верно"] + out["грубый промах"]
    out["decisive"] = decisive
    out["accuracy"] = (float(out["верно"]) / decisive) if decisive else None
    out["hedge_rate"] = (float(out["уточнение"]) / out["n"]) if out["n"] else None
    out["fail_rate"] = (float(out["сбой"]) / out["n"]) if out["n"] else None
    return out


def format_report(sc, corpus_version):
    lines = ["Разборщик лотов на корпусе %s: %d записей" % (corpus_version, sc["n"])]
    if sc["accuracy"] is None:
        lines.append("  решительных ответов нет — точность не определена")
    else:
        lines.append("  точность на решительных ответах: %.0f%% (%d из %d)"
                     % (100 * sc["accuracy"], sc["верно"], sc["decisive"]))
    lines.append("  уточнений: %d (%.0f%%)  |  сбоев: %d (%.0f%%)"
                 % (sc["уточнение"], 100 * (sc["hedge_rate"] or 0),
                    sc["сбой"], 100 * (sc["fail_rate"] or 0)))
    if sc["misses"]:
        lines.append("  грубые промахи:")
        for m in sc["misses"]:
            lines.append("    %s  ждали %s, получили %s — %s"
                         % (m["cid"], m["expect"], m["got"], m["title"][:52]))
    return "\n".join(lines)


async def run_entry(entry, client):
    from crawler.scripts.investigator import investigate, _row_to_tender
    row = (client.table("tenders")
           .select("id,external_id,title,organization,price,currency,deadline,"
                   "source,source_url,search_text,message_type,extra_info")
           .eq("alert_seq", entry["alert_seq"]).limit(1).execute().data or [None])[0]
    if not row:
        return {"cid": entry["cid"], "expect": entry["expect"], "got": None,
                "outcome": "сбой", "title": entry["title"], "note": "лот исчез из БД"}
    v = None
    try:
        v = await investigate(_row_to_tender(row), client)
    except Exception as exc:
        return {"cid": entry["cid"], "expect": entry["expect"], "got": None,
                "outcome": "сбой", "title": entry["title"], "note": str(exc)[:120]}
    got = (v or {}).get("verdict")
    return {"cid": entry["cid"], "expect": entry["expect"], "got": got,
            "outcome": classify(entry["expect"], got), "title": entry["title"],
            "why": ((v or {}).get("why") or "")[:300],
            "cost": (v or {}).get("_cost_usd"), "turns": (v or {}).get("_turns")}


async def main(limit, only_cid, send):
    from crawler.core.db import _get_client
    entries, meta = load_corpus()
    if only_cid:
        entries = [e for e in entries if e["cid"] == only_cid]
    if limit:
        entries = entries[:limit]
    client = _get_client()
    results = []
    for e in entries:
        r = await run_entry(e, client)
        results.append(r)
        print("  %-8s %-13s -> %-13s %s"
              % (r["cid"], r["expect"], r["got"] or "—", r["outcome"]))
    sc = score(results)
    report = format_report(sc, meta.get("corpus_version", "?"))
    print("\n" + report)
    spent = sum(float(r.get("cost") or 0) for r in results)
    print("  потрачено: $%.2f" % spent)
    _append_log(sc, meta, results, spent)
    if send:
        _send(report, spent)
    return 0


def _append_log(sc, meta, results, spent):
    rec = {"ts": datetime.now(timezone.utc).isoformat(),
           "corpus_version": meta.get("corpus_version"),
           "model": getattr(settings, "ai_relevance_model", None),
           "n": sc["n"], "accuracy": sc["accuracy"], "hedge_rate": sc["hedge_rate"],
           "fail_rate": sc["fail_rate"], "cost_usd": round(spent, 3),
           "misses": [{"cid": m["cid"], "expect": m["expect"], "got": m["got"]}
                      for m in sc["misses"]],
           "verdicts": [{"cid": r["cid"], "got": r["got"]} for r in results]}
    try:
        os.makedirs(os.path.dirname(_LOG), exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("  записано в %s" % _LOG)
    except Exception as exc:
        print("  ЛОГ НЕ ЗАПИСАН (%s) — сравнить с прошлым прогоном будет нечем"
              % str(exc)[:80])


def _send(report, spent):
    import httpx
    try:
        httpx.post("https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                   json={"chat_id": settings.telegram_alert_chat_id,
                         "text": "\U0001f9ea *Бенчмарк разборщика*\n" + report
                                 + "\n\nстоимость прогона: $%.2f" % spent,
                         "parse_mode": "Markdown"}, timeout=15)
    except Exception as exc:
        print("send failed: %s" % str(exc)[:120])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--cid", default="")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.limit, a.cid, a.send)))
