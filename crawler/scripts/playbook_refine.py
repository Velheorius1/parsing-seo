"""playbook_refine — distil human corrections into generalized relevance principles
(classifier_playbook). Phase 2 of TZ docs/plans/2026-06-07-classifier-playbook-loop-TZ.md.

Flow (TZ §2.2): fetch corrections -> LLM classify+distil (NO proper names) -> deterministic
signal_key -> dedup (support_count++ / insert candidate) -> promote candidate->active at
support_count>=2 (unless --bootstrap) -> report (TG --send). NEVER deletes (retire only).

Model: settings.ai_relevance_model. До 02.08 здесь стоял deepseek-v4-pro с
пометкой «strong, not flash» со ссылкой на правило feedback_no_dumb_model_for_savings;
02.08 Данияр решил перевести весь конвейер на одну модель flash-0731, и дистилляция
поехала вместе с ним. Прежнее решение отменено сознательно, а не потеряно.
Usage: --days N (default 30), --bootstrap (all stay candidate), --send (TG report).
"""
import argparse, json, re, sys
sys.path.insert(0, "/opt/parsing-seo")
import httpx
from crawler.config.settings import settings
from crawler.core.feedback import RECALL_TAXONOMY
from supabase import create_client

TAXONOMY = [RECALL_TAXONOMY, "ad-as-client", "irrelevant-niche", "wrong-score", "trivial"]

# Куда снимать отказную коррекцию, которую модель по ошибке пометила recall-таксономией.
# Направление известно ДЕТЕРМИНИРОВАННО — по метке человека, гадать не нужно.
_REJECT_FALLBACK = {"ad": "ad-as-client", "irrelevant": "irrelevant-niche"}

# Controlled signal_slug vocabulary (E-F fix, 2026-07-06). The free-form slug made
# every correction a UNIQUE signal_key → support stuck at 1 → nothing ever promoted
# (201 clicks, 0 active principles). A closed list groups similar corrections so
# support accumulates and promotion works. Derived from Daniyar's real click themes.
SIGNAL_SLUGS = [
    "self-promo",          # реклама/самопиар своих услуг
    "vacancy",             # поиск работника/исполнителя
    "non-print-goods",     # техника/товары/мебель — не полиграфия
    "textile-sewing",      # пошив изделий (не печать логотипа)
    "cutting-outdoor",     # резка/наружка/конструкции
    "greeting-offtopic",   # приветствие/лозунг/оффтоп
    "medicine-food",       # лекарства/продукты как товар
    "construction",        # стройматериалы/строительные работы
    "services-nonprint",   # услуги вне печати (IT/СММ/ивенты/дизайн-без-печати)
    "print-rejected",      # наш полиграф-заказ ошибочно зарезан
    "packaging-rejected",  # упаковка ошибочно зарезана
    "score-borderline",    # балл на границе ниши
    "other",
]

_DISTIL_PROMPT = """Ты дистиллируешь ОДНУ человеческую коррекцию классификатора полиграф-тендеров в ОБОБЩЁННЫЙ принцип.

Коррекция:
Текст тендера: "%s"
Система решила: %s
Человек исправил на: %s

Здесь человек ОТКЛОНИЛ показанный лот, поэтому принцип может быть только СУЖАЮЩИМ.
Таксономия (выбери ОДНУ): ad-as-client (реклама принята за заказ), irrelevant-niche (вне ниши принято за наше), wrong-score (близко но балл кривой), trivial (разовая мелочь — не возводить в принцип).
Варианта «наш заказ зарезан» здесь НЕТ — это противоположный случай, он разбирается отдельным промптом.

Сформулируй ПРИНЦИП — обобщённый признак ошибки, КАК ДУМАТЬ (не «что блокировать»), БЕЗ имён собственных (компаний/каналов/доменов). Конкретика — только в example.
signal_slug — ВЫБЕРИ РОВНО ОДИН из списка (не придумывай свой): self-promo, vacancy,
non-print-goods, textile-sewing, cutting-outdoor, greeting-offtopic, medicine-food,
construction, services-nonprint, print-rejected, packaging-rejected, score-borderline, other.

effect — что ДЕЛАЕТ твой принцип с сетью отбора: "narrow" (резать больше) или "widen" (ловить больше).

СТРОГО JSON: {"taxonomy":"...","principle":"...","example":"(пример: ...)","signal_slug":"...","effect":"narrow"}
Если trivial — taxonomy:"trivial", остальное пустое."""

# Readable phrase for the system verdict token stored in original_label (Hole A fix).
_VERDICT_RU = {
    "client": "релевантный (наш заказ)",
    "alerted": "релевантный (показан)",
    "weak": "слабо-релевантный (низкий балл)",
    "ad": "реклама", "irrelevant": "вне ниши",
}

# Recall-guard vocabulary: when the system UNDER-rated a lot the human confirmed as ours.
_PROTECT_SLUGS = ["print-underrated", "packaging-underrated", "niche-term-missed", "format-underrated", "other"]

_DISTIL_PROMPT_PROTECT = """Система НЕДООЦЕНИЛА реальный полиграф-заказ (recall-промах): показала слабо/как чужое, а человек подтвердил — ЭТО НАШ.

Текст тендера: "%s"
Система оценила: %s
Человек: НАШ заказ (client)

Сформулируй ЗАЩИТНЫЙ ПРИНЦИП — обобщённый признак, ПО КОТОРОМУ такой лот НАДО ловить (что система упускает), КАК ДУМАТЬ, БЕЗ имён собственных. Конкретика — только в example.
ЗАПРЕЩЕНО писать принцип вида «это НЕ наш заказ» / «не является полиграфией» — человек уже сказал, что заказ НАШ. Принцип обязан РАСШИРЯТЬ сеть, а не сужать.
signal_slug — ВЫБЕРИ РОВНО ОДИН: print-underrated, packaging-underrated, niche-term-missed, format-underrated, other.
effect — что ДЕЛАЕТ твой принцип с сетью отбора: "widen" (ловить больше) или "narrow" (резать больше). Здесь корректен только "widen".

СТРОГО JSON: {"taxonomy":"relevant-rejected","principle":"...","example":"(пример: ...)","signal_slug":"...","effect":"widen"}"""

_PROPER_NAME = re.compile(r"[A-ZА-ЯЁ][a-zа-яё]{2,}\s+[A-ZА-ЯЁ]|\.(uz|ru|com)\b|«[^»]*[A-ZА-ЯЁ]{2,}", re.U)

def _client():
    return create_client(settings.supabase_url, settings.supabase_service_role_key)

def _classify(verdict, human):
    # type: (str, str) -> str
    """Direction of the learning signal from ONE feedback click, or "" to skip.

    alert_feedback holds only SHOWN items (original_label now carries the system
    VERDICT, not message_type — Hole A fix 2026-07-16), so:
      human ad/irrelevant                        -> 'reject'  (false positive: shown, shouldn't be)
      human client + weak/ad/irrelevant verdict  -> 'protect' (recall guard: system underrated it)
      human client + relevant verdict            -> agreement -> skip (no signal, saves an LLM call)
    A false NEGATIVE proper (a tender never shown) cannot appear here by construction —
    that gap is covered by recall_audit (V3), not by clicks.
    """
    v = (verdict or "").strip().lower()
    h = (human or "").strip().lower()
    if h in ("ad", "irrelevant"):
        return "reject"
    if h == "client" and v in ("ad", "irrelevant", "weak"):
        return "protect"
    return ""

def fetch_corrections(client, days):
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    r = (client.table("alert_feedback").select("message_text,original_label,corrected_label,created_at")
         .not_.is_("message_text", "null").gte("created_at", since).execute())
    out = []
    for x in (r.data or []):
        if not x.get("message_text"):
            continue
        direction = _classify(x.get("original_label"), x.get("corrected_label"))
        if not direction:
            continue  # agreement — nothing to learn from a confirmation
        x["direction"] = direction
        out.append(x)
    return out

def distil(text, verdict, human, direction):
    verdict_ru = _VERDICT_RU.get((verdict or "").strip().lower(), verdict or "?")
    if direction == "protect":
        prompt = _DISTIL_PROMPT_PROTECT % (text[:300], verdict_ru)
    else:
        prompt = _DISTIL_PROMPT % (text[:300], verdict_ru, human)
    try:
        resp = httpx.post("https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": "Bearer %s" % settings.openrouter_api_key},
            json={"model": settings.ai_relevance_model, "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 600, "temperature": 0, "response_format": {"type": "json_object"},
                  # ОБЯЗАТЕЛЬНО (error-log 06-29, здесь пропущено с рождения скрипта):
                  # deepseek-v4-* — рассуждающие модели, и без этого флага рассуждение
                  # съедает ВЕСЬ бюджет: замер 04.08 на живом фидбеке — finish=length,
                  # reasoning_tokens=600 из 600, content='' → distil() возвращает None
                  # на КАЖДОЙ коррекции. Расширение бюджета не лечит: на 2000 токенов
                  # рассуждение съедает и 2000. С флагом — валидный JSON за 206 токенов.
                  # Дефект НЕ от смены модели 02.08: прежняя deepseek-v4-pro на этом же
                  # промпте отдавала такой же пустой ответ. То есть playbook не учился
                  # ВООБЩЕ — 358 коррекций за 30 дней ушли в мусор, отсюда «+0 принципов
                  # за 7д» в скоркарте источников.
                  "reasoning": {"enabled": False}},
            timeout=40)
        if resp.status_code != 200:
            return None
        content = resp.json()["choices"][0]["message"]["content"] or ""
        m = re.search(r"\{.*\}", content, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None

def enforce_direction(taxonomy, direction, human):
    # type: (str, str, str) -> str
    """Свести таксономию принципа с НАПРАВЛЕНИЕМ коррекции. Чистая функция.

    Направление известно достоверно — из метки человека; таксономию выбирает модель.
    Отказная коррекция (человек снял лот) по построению не может родить recall-принцип
    «наш заказ зарезан», но формулировка в промпте была похожа на «система решила
    релевантный → человек отклонил», и модели было куда промахнуться. Цена промаха
    несимметрична: recall-принципы закрепляются в промпте ПЕРВЫМИ и ВСЕ до одного
    (get_relevance_playbook), тогда как отказные конкурируют за остаток слотов.
    """
    if direction == "protect":
        return RECALL_TAXONOMY
    if taxonomy == RECALL_TAXONOMY:
        return _REJECT_FALLBACK.get((human or "").strip().lower(), "irrelevant-niche")
    return taxonomy


_EXPECTED_EFFECT = {"protect": "widen", "reject": "narrow"}


def effect_conflicts(effect, direction):
    # type: (str, str) -> bool
    """Принцип заявил противоположное тому, что сказал человек? Чистая функция.

    Самопроверка нужна потому, что дистиллятор УЖЕ выворачивал коррекции наизнанку —
    и это не гипотеза, а сверка с исходными кликами (10.08):

      «Roll up kerak 150x200 cm»          → человек: НАШ  → принцип: «не является
                                                             заказом на полиграфию»
      «Бумага и изделия из бумаги»        → человек: НАШ  → принцип: «закупка товара,
                                                             а не наш тендер»
      «Ручка металлическая»               → человек: НАШ  → принцип: «мы не принимаем
                                                             заказы на поставку»
      «Нужны крафт пакеты с печатью»      → человек: НАШ  → принцип: «относится к
                                                             поставке упаковки»

    Все четыре осели в recall-таксономии на support_count=1 — то есть одна следующая
    совпавшая коррекция сделала бы их активными, и промпт получил бы первыми строками
    ровно те принципы, против которых человек кликал. Родом они из эпохи до появления
    protect-ветки, когда ВСЕ коррекции шли через отказный промпт, но сама возможность
    инверсии этим не закрыта — закрывает её вот эта проверка.

    Fail-open: поле не пришло или незнакомое — пропускаем. Молчаливая остановка
    обучения уже стоила 358 коррекций (см. reasoning:false выше), второй раз не надо.
    """
    e = (effect or "").strip().lower()
    if e not in ("widen", "narrow"):
        return False
    want = _EXPECTED_EFFECT.get(direction)
    return bool(want) and e != want


def pinned_recall_principles(client):
    # type: (object) -> list
    """Активные recall-принципы — те, что уходят в промпт первыми и безусловно.
    Печатаются каждый прогон: это единственный список, который влияет на КАЖДУЮ
    оценку релевантности, и он должен быть перед глазами, а не только в БД."""
    try:
        return (client.table("classifier_playbook").select("id,signal_key,support_count,principle")
                .eq("taxonomy", RECALL_TAXONOMY).eq("status", "active")
                .order("support_count", desc=True).execute().data or [])
    except Exception:
        return []


def main(days, bootstrap, send):
    client = _client()
    corr = fetch_corrections(client, days)
    print("corrections in %dd: %d" % (days, len(corr)))
    n_reject = sum(1 for c in corr if c.get("direction") == "reject")
    n_protect = sum(1 for c in corr if c.get("direction") == "protect")
    print("  directions: reject(FP)=%d protect(recall)=%d" % (n_reject, n_protect))
    inserted = updated = skipped = promoted = misfiled = inverted = 0
    proposals = []
    for c in corr:
        direction = c.get("direction") or "reject"
        d = distil(c["message_text"], c.get("original_label") or "?", c["corrected_label"], direction)
        if not d or d.get("taxonomy") not in TAXONOMY:
            skipped += 1; continue
        if d["taxonomy"] == "trivial":
            skipped += 1; continue
        if effect_conflicts(d.get("effect"), direction):
            # принцип говорит обратное тому, что сказал человек — в playbook не пускаем
            inverted += 1; skipped += 1
            proposals.append("[инверсия/%s] %s" % (direction, (c["message_text"] or "")[:60]))
            continue
        fixed = enforce_direction(d["taxonomy"], direction, c.get("corrected_label") or "")
        if fixed != d["taxonomy"] and direction != "protect":
            misfiled += 1
        d["taxonomy"] = fixed
        principle = (d.get("principle") or "").strip()
        if not principle or _PROPER_NAME.search(principle):
            # proper-name linter failed -> systemic, send to proposals not playbook
            proposals.append("[%s] %s" % (d["taxonomy"], (c["message_text"] or "")[:60]))
            skipped += 1; continue
        allowed = _PROTECT_SLUGS if direction == "protect" else SIGNAL_SLUGS
        slug = (d.get("signal_slug") or "other").strip().lower()
        if slug not in allowed:
            slug = "other"  # snap free-form back to the controlled vocab
        signal_key = "%s:%s" % (d["taxonomy"], slug)
        ex = (client.table("classifier_playbook").select("id,support_count,status")
              .eq("signal_key", signal_key).limit(1).execute().data)
        if ex:
            row = ex[0]; sc = (row.get("support_count") or 1) + 1
            upd = {"support_count": sc}
            if not bootstrap and sc >= 2 and row.get("status") == "candidate":
                upd["status"] = "active"; promoted += 1
            client.table("classifier_playbook").update(upd).eq("id", row["id"]).execute()
            updated += 1
        else:
            client.table("classifier_playbook").insert({
                "taxonomy": d["taxonomy"], "principle": principle,
                "example": d.get("example"), "signal_key": signal_key,
                "status": "candidate", "support_count": 1}).execute()
            inserted += 1
    summary = ("playbook_refine: corrections=%d inserted=%d updated=%d promoted=%d skipped=%d "
               "misfiled=%d inverted=%d | proposals=%d"
               % (len(corr), inserted, updated, promoted, skipped, misfiled, inverted, len(proposals)))
    print(summary)
    if proposals:
        print("PROMPT PROPOSALS (systemic / proper-name / инверсия — not playbook):")
        for p in proposals[:10]: print("  -", p)
    pinned = pinned_recall_principles(client)
    print("RECALL PINNED (в промпте первыми, безусловно): %d" % len(pinned))
    for r in pinned:
        print("  - #%s %s (sup=%s) %s" % (r.get("id"), r.get("signal_key"),
                                          r.get("support_count"), (r.get("principle") or "")[:90]))
    if send and (inserted or promoted or proposals):
        body = "\U0001f4d8 *Playbook refine*\n" + summary
        if inverted:
            body += ("\n\n⚠️ Инверсий отброшено: %d — дистиллятор писал принцип "
                     "против метки человека." % inverted)
        if proposals:
            body += "\n\n*Prompt proposals* (не playbook):\n" + "\n".join("• " + p for p in proposals[:8])
        try:
            httpx.post("https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                json={"chat_id": settings.telegram_alert_chat_id, "text": body, "parse_mode": "Markdown"},
                timeout=10)
        except Exception:
            pass

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--bootstrap", action="store_true", help="all stay candidate (no auto-active)")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    main(a.days, a.bootstrap, a.send)
