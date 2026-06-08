"""Telegram alert notifier — sends new matching tenders to a Telegram chat.

Pipeline: deadline filter → keyword match → AI relevance check (Qwen via OpenRouter) → send.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx

from crawler.config.settings import settings
from crawler.core.ai_decision_log import log_ai_decision
from crawler.core.models import RawTender

logger = logging.getLogger(__name__)

# ── AI relevance filter ──────────────────────────────────────────

# Valid category labels — must match parsing_feedback CLI labels.
_VALID_CATEGORIES = ("client", "ad", "irrelevant")


@dataclass
class RelevanceResult:
    """Outcome of AI relevance check.

    is_relevant: True if score >= ai_score_threshold (or fallback-allow on error).
    score/category/reason: NULL when AI failed/unavailable — caller should not
    persist NULL values as "0" or "irrelevant".
    """

    is_relevant: bool
    score: Optional[int] = None
    category: Optional[str] = None
    reason: Optional[str] = None

    def __bool__(self) -> bool:
        # Backward-compat for callers that historically did `if await _ai_check_relevance(...)`
        return self.is_relevant


_RELEVANCE_PROMPT = """Наша компания — ТИПОГРАФИЯ и УПАКОВОЧНОЕ производство в Узбекистане.

МЫ ДЕЛАЕМ:
- Коробки (гофро, картон, подарочные, совга кутилар)
- Этикетки, стикеры, наклейки
- Полиграфия (каталоги, брошюры, блокноты, визитки, буклеты)
- Выставочные стенды, информационные таблички/указатели, бейджи, бланки строгого учёта
- Издательские и типографские услуги (печать книг, документов, периодических изданий)
- Пакеты (полиэтилен, крафт)
- Постеры, плакаты, интерьерная печать (bosma, pechat)
- Сувенирная продукция (ручки, флешки, ежедневники, кружки)
- Печать на футболках, флагах, лентах, ткани (DTF, сублимация)
- UV печать (на фомиксе, пластике, стекле)
- Ламинирование, переплёт
- Пластиковые карты (скидочные, дисконтные)

НЕ НАШЕ:
- Наружные баннеры/билборды/растяжки на фасадах, световые короба, монтаж наружной рекламы
  (НО информационные таблички, выставочные стенды, указатели — это НАШЕ)
- Папки, скоросшиватели, канцелярские органайзеры
- Широкоформатная печать ТОЛЬКО для наружной рекламы (билборды, растяжки)
- Оракал, плоттерная резка для рекламных конструкций
- Оклейка авто, тонировка, плёнки
- Фомикс, акрил, алюкобонд (рекламные конструкции)
- Вакансии, поиск работы, набор сотрудников
- Видеосъёмка, фото, SMM, реклама в Google/Instagram
- IT-услуги, разработка сайтов
- Организация мероприятий, ивенты
- Стройматериалы (лестницы, леса)
- Мебель, оборудование, станки, ремонт принтеров
- Только дизайн без печати
- Реклама ЧУЖИХ услуг ("мы делаем...", "звоните нам...")
- Мелкий заказ (менее 50 штук)
- Закупка готовых книг, учебников, тетрадей (не печать на заказ)
- Подписка и доставка периодических печатных изданий (газеты, журналы)
- Марля полиграфическая, расходные материалы для типографии
{playbook}
Объявление:
Название: {title}
Заказчик: {organization}
Позиции лота: {details}

ВАЖНО (мульти-лот): если в лоте НЕСКОЛЬКО позиций и ХОТЯ БЫ ОДНА явно наша
(печать, упаковка, книги/издания, бланки, бейджи, таблички, стенды) — оцени
лот как НАШ (score >= 70), даже если остальные позиции не наши.

Ответь СТРОГО в JSON (только JSON, без пояснений и markdown):
{{"score": <0-100>, "category": "<client|ad|irrelevant>", "reason": "<до 100 символов>"}}

score: 90-100 = точно наш заказ; 70-89 = вероятно наш; 40-69 = смежная область;
       0-39 = точно не наш.
category: "client" = реальный заказчик хочет купить; "ad" = реклама чужих услуг;
          "irrelevant" = не наша область вообще.
/no_think"""


def _allow(reason: str = "") -> RelevanceResult:
    """Fallback: let tender through but do not persist a fake score."""
    return RelevanceResult(is_relevant=True, score=None, category=None, reason=None)


def _strip_think_tags(text: str) -> str:
    """Remove Qwen3 <think>...</think> blocks if present."""
    if "<think>" not in text:
        return text.strip()
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _extract_json_object(text: str) -> Optional[dict]:
    """Find first {...} JSON object in text and parse it. Tolerates code fences."""
    if not text:
        return None
    # Strip ```json ... ``` fences
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        candidate = match.group(0)
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_relevance_payload(payload: dict) -> Optional[RelevanceResult]:
    """Validate AI JSON payload → RelevanceResult. None on bad shape."""
    raw_score = payload.get("score")
    try:
        score = int(raw_score)
    except (TypeError, ValueError):
        return None
    if score < 0 or score > 100:
        # Clamp out-of-range AI output rather than fail hard
        score = max(0, min(100, score))

    raw_cat = payload.get("category", "")
    category = str(raw_cat).strip().lower() if raw_cat is not None else ""
    if category not in _VALID_CATEGORIES:
        # AI sometimes returns "tender" / "spam" — coerce by score:
        # high score → client, low → irrelevant.
        category = "client" if score >= settings.ai_score_threshold else "irrelevant"

    reason = str(payload.get("reason") or "")[:200].strip()

    is_relevant = score >= settings.ai_score_threshold
    return RelevanceResult(
        is_relevant=is_relevant,
        score=score,
        category=category,
        reason=reason,
    )


async def _ai_call_one(
    tender: RawTender,
    client: httpx.AsyncClient,
    model: str,
    role: str = "unknown",
) -> Optional[RelevanceResult]:
    """Single OpenRouter call. Returns RelevanceResult on success, None on
    network/parse failure (caller decides how to fall back).

    `role` is "fast" or "max" — used only for JSONL comparison logging.
    """
    import time as _time

    from crawler.core.feedback import get_relevance_playbook
    _pb = get_relevance_playbook()
    _pb_block = ("\nПРИНЦИПЫ (из обратной связи; при конфликте важнее единичных примеров):\n%s\n" % _pb) if _pb else ""
    # Item names give the model the actual products for generic-title /
    # multi-item lots (e.g. Hayotbirja "Отбор" where title="Отбор" but the
    # body lists "Книги печатные"). Drop the raw good_maps JSON blob.
    _details = (tender.search_text or "").split("{", 1)[0].strip()[:320]
    prompt = _RELEVANCE_PROMPT.format(
        title=tender.title[:300],
        organization=tender.organization or "",
        details=_details,
        playbook=_pb_block,
    )

    _t0 = _time.monotonic()
    http_status: Optional[int] = None
    error: Optional[str] = None
    result: Optional[RelevanceResult] = None
    try:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": "Bearer %s" % settings.openrouter_api_key,
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                # Token budget зависит от роли:
                # - fast (deepseek-v4-flash): 400 хватает, не reasoning model
                # - max (deepseek-v4-pro): 1200 — это reasoning model с hybrid
                #   attention, reasoning токены съедают бюджет до text. Compare
                #   на 1 день после первичного fix (max_tokens=400) показал
                #   no_json=20% + empty_answer=20% именно на max model.
                "max_tokens": 1200 if role == "max" else 400,
                "temperature": 0,
                # deepseek-v4-pro (max role) is a reasoning model: reasoning
                # tokens ate the budget -> empty_answer/no_json ~20% -> score=None
                # -> fail-open (junk like startup announcements alerted). Disable
                # reasoning: this is product-scope classification, not a reasoning
                # task. Verified 2026-06-08.
                "reasoning": {"enabled": False},
                # OpenRouter structured output — enforce JSON object для моделей
                # поддерживающих response_format (DeepSeek / OpenAI / большинство Qwen).
                "response_format": {"type": "json_object"},
            },
            # max model медленнее (V4 Pro reasoning p95=18s observed) — даём
            # запас. Fast не страдает от этого таймаута (p95=10s).
            timeout=30 if role == "max" else 20,
        )
        http_status = resp.status_code
        if resp.status_code != 200:
            logger.warning("[AI Filter:%s] OpenRouter %d: %s", model, resp.status_code, resp.text[:100])
            error = "http_%d: %s" % (resp.status_code, resp.text[:80])
            return None
        data = resp.json()
        raw = data["choices"][0]["message"]["content"] or ""
        answer = _strip_think_tags(raw)
        if not answer:
            error = "empty_answer"
            return None
        payload = _extract_json_object(answer)
        if not payload:
            logger.warning("[AI Filter:%s] No JSON: %s", model, answer[:120])
            error = "no_json: %s" % answer[:80]
            return None
        result = _parse_relevance_payload(payload)
        if result is None:
            error = "bad_payload"
        return result
    except Exception as exc:
        logger.warning("[AI Filter:%s] Error: %s", model, str(exc)[:80])
        error = "exception: %s" % str(exc)[:80]
        return None
    finally:
        latency_ms = int((_time.monotonic() - _t0) * 1000)
        # NB: use `is not None`, not truthy check — RelevanceResult.__bool__
        # returns is_relevant, so `if result` is False for rejected tenders
        # (score=0, category=irrelevant), which would null out the log.
        log_ai_decision(
            model=model,
            role=role,
            tender_external_id=getattr(tender, "external_id", None),
            source=getattr(tender, "source", "") or "",
            title=tender.title or "",
            organization=getattr(tender, "organization", "") or "",
            is_relevant=(result.is_relevant if result is not None else None),
            score=(result.score if result is not None else None),
            category=(result.category if result is not None else None),
            reason=(result.reason if result is not None else None),
            latency_ms=latency_ms,
            http_status=http_status,
            error=error,
        )


async def _ai_check_relevance(
    tender: RawTender,
    client: httpx.AsyncClient,
) -> RelevanceResult:
    """Hybrid relevance check.

    1. Fast model (qwen3-30b-a3b, ~$0.00007/call) decides first.
    2. If fast accepts → trust it, return.
    3. If fast rejects → second opinion from Max model
       (qwen3.6-max-preview, ~$0.0046/call). Max wins on conflict.
    4. If fast errors → fall back to Max only.

    ~95% of calls handled by fast model alone, escapes to Max only on
    rejection. Saves ~10x vs always-Max while preserving precision on edge
    cases (e.g. "Услуги печатные и копированию звуко- и видеозаписей").
    """
    if not settings.openrouter_api_key:
        return _allow("no_key")

    fast_model = settings.ai_relevance_model_fast or ""
    max_model = settings.ai_relevance_model

    # Hybrid disabled (empty fast): single-model legacy path.
    if not fast_model:
        result = await _ai_call_one(tender, client, max_model, role="max")
        if result is None:
            return _allow("max_failed")
        if not result.is_relevant:
            logger.info(
                "[AI Filter:max] REJECTED score=%d cat=%s: %s (%s)",
                result.score, result.category,
                tender.title[:60], (result.reason or "")[:80],
            )
        return result

    # Fast pass.
    fast_result = await _ai_call_one(tender, client, fast_model, role="fast")
    if fast_result is None:
        # Fast failed → fall through to Max.
        max_result = await _ai_call_one(tender, client, max_model, role="max")
        if max_result is None:
            return _allow("both_failed")
        if not max_result.is_relevant:
            logger.info(
                "[AI Filter:max-fallback] REJECTED score=%d cat=%s: %s",
                max_result.score, max_result.category, tender.title[:60],
            )
        return max_result

    if fast_result.is_relevant:
        # Fast accepts → done. Cheap path covers ~95% of incoming tenders.
        return fast_result

    # Fast rejected → second opinion from Max (catches "печатные… копированию
    # звуко-видеозаписей" type tenders that a3b false-rejects ~4% of the time).
    max_result = await _ai_call_one(tender, client, max_model, role="max")
    if max_result is None:
        # Max unavailable → trust fast rejection.
        logger.info(
            "[AI Filter:fast] REJECTED (max unavailable) score=%d cat=%s: %s (%s)",
            fast_result.score, fast_result.category,
            tender.title[:60], (fast_result.reason or "")[:80],
        )
        return fast_result

    if max_result.is_relevant and not fast_result.is_relevant:
        logger.info(
            "[AI Filter:override] Max OVERRIDES fast: fast=%d max=%d cat=%s: %s",
            fast_result.score, max_result.score, max_result.category, tender.title[:60],
        )
    elif not max_result.is_relevant:
        logger.info(
            "[AI Filter:hybrid] REJECTED fast=%d max=%d cat=%s: %s (%s)",
            fast_result.score, max_result.score, max_result.category,
            tender.title[:60], (max_result.reason or "")[:80],
        )
    return max_result

# ── Deadline filter ──────────────────────────────────────────────

# Common date patterns in tender deadlines
_DATE_PATTERNS = [
    (re.compile(r"(\d{2})\.(\d{2})\.(\d{4})"), "%d.%m.%Y"),   # 15.05.2023
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "%Y-%m-%d"),     # 2023-05-15
    (re.compile(r"(\d{2})/(\d{2})/(\d{4})"), "%d/%m/%Y"),     # 15/05/2023
]


def _parse_deadline(deadline_str: Optional[str]) -> Optional[datetime]:
    """Try to parse a deadline string into a datetime. Returns None if unparseable.

    If string contains 'Истекает'/'expires'/'deadline', use the date after that keyword.
    Otherwise use the LAST date found (most likely the expiry, not published date).
    """
    if not deadline_str:
        return None

    # If string contains expiry keyword, only search after it
    text = deadline_str
    for kw in ("Истекает", "истекает", "expires", "Expires", "deadline", "Deadline", "до "):
        idx = text.find(kw)
        if idx >= 0:
            text = text[idx:]
            break

    # Find ALL dates and take the last one (most likely expiry)
    last_dt = None
    for pattern, fmt in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            try:
                last_dt = datetime.strptime(m.group(0), fmt)
            except ValueError:
                continue
    if last_dt:
        return last_dt

    # Fallback: search original string for last date
    if text != deadline_str:
        for pattern, fmt in _DATE_PATTERNS:
            for m in pattern.finditer(deadline_str):
                try:
                    last_dt = datetime.strptime(m.group(0), fmt)
                except ValueError:
                    continue
    return last_dt


def _is_deadline_expired(tender: RawTender) -> bool:
    """Check if tender deadline has already passed. Returns False if no deadline."""
    dt = _parse_deadline(tender.deadline)
    if dt is None:
        return False  # no deadline or unparseable = let it through
    return dt < datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)  # 1 day grace period


# Minimum stem length for fuzzy matching (Russian word roots)
_MIN_STEM = 4


def _get_keywords() -> List[str]:
    """Parse comma-separated keywords from settings."""
    raw = settings.alert_keywords or ""
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


def _stem(word: str) -> str:
    """Crude Russian stemming — trim to root for matching.

    'упаковка' -> 'упаков', 'полиграфия' -> 'полиграф', 'печать' -> 'печат'
    Short words (<= _MIN_STEM) kept as-is.
    """
    if len(word) <= _MIN_STEM:
        return word
    # Strip common Russian suffixes (longest first)
    for suffix in ("ция", "ия", "ка", "ок", "ей", "ов", "ть", "ые", "ой", "ая", "ое", "а", "о", "е", "и", "у", "ы"):
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return word[: -len(suffix)]
    return word


def _word_start_match(text: str, stem: str) -> int:
    """Find stem at word boundary (start of word). Return index or -1."""
    start = 0
    while True:
        idx = text.find(stem, start)
        if idx == -1:
            return -1
        # Stem must be at start of a word: preceded by non-alpha or string start
        if idx == 0 or not text[idx - 1].isalpha():
            return idx
        start = idx + 1


# False positive patterns: if stem is followed by these strings, skip the match
_FALSE_POSITIVES = {
    "календар": [" кун", "кун ", " дн", " день"],  # "календарных дней/кун" = time, not product
}

# Слабые ключи (стемы): высокочастотные омонимы, которые на мультилотовых
# reverse-аукционах ловят чужую отрасль. Матч ТОЛЬКО по слабому ключу проходит
# лишь если рядом нет дисквалификатора (_NEGATIVE_STEMS). Сильные ключи
# (печат/этикет/упаков/коробк/картон/брошюр…) сюда НЕ входят и пропускаются
# безусловно — это защищает от false negatives.
_WEAK_STEMS = frozenset({
    "лент",       # лента/ленты → изолента, лента светоотражающая/малярная, конвейерная
    "гофр",       # гофра → кабельная гофра/гофрошланг (гофрокороб остаётся через др. ключи)
    "магнит",     # электромагнит, магнитный пускатель, магнитная муфта
    "папк",       # папка → офисный скоросшиватель
    "плёнк", "пленк", "plyonka",  # плёнка → теплица/стретч/термоусадка
})

# Дисквалификаторы — слова заведомо чужих отраслей (металл/электрика/стройка/ДВС).
# НЕ включаем end-use слова ("кабельн", "дорожн") — упаковка/печать МОЖЕТ быть
# для кабеля или дорожной отрасли. Только сам продукт-чужак.
_NEGATIVE_STEMS = (
    "сварочн", "электрод", "растворител", "разбавител", "пускател", " реле",
    "турбокомпресс", "шестерн", "подшипник", "двигател", "генератор",
    "трансформатор", "арматур", "задвижк", "насос", "редуктор",
    "видеонаблюд", "светоотраж", "изолент", "краскораспылит", "окрасочн",
)


def _has_negative_context(text: str) -> bool:
    """True if text contains a wrong-industry disqualifier stem."""
    return any(neg in text for neg in _NEGATIVE_STEMS)


def _find_matching_keyword(tender: RawTender, keywords: List[str]) -> Optional[str]:
    """Return first matching keyword or None.

    Uses stem-based matching with word-boundary check to avoid
    false positives like 'зонт' in 'горизонтал'.

    Weak-keyword gate (2026-05-29): a match coming ONLY from a weak/ambiguous
    keyword (_WEAK_STEMS) is dropped if the lot also contains a wrong-industry
    disqualifier (_NEGATIVE_STEMS) — kills multi-item reverse-auction FPs like
    "Лента светоотражающая, … электрод сварочный" matching on «лента». Strong
    keywords are never gated, so real print/packaging tenders still pass.
    """
    text = (tender.search_text + " " + tender.title).lower()
    has_negative = _has_negative_context(text)
    for kw in keywords:
        stem = _stem(kw) if len(kw) > _MIN_STEM else kw

        if len(stem) < _MIN_STEM:
            # Short keywords: exact match only
            if _word_start_match(text, kw) < 0:
                continue
        else:
            idx = _word_start_match(text, stem)
            if idx < 0:
                continue

            # Check false positive exclusions
            excl = _FALSE_POSITIVES.get(stem)
            if excl:
                after = text[idx + len(stem):idx + len(stem) + 10]
                if any(after.startswith(fp) for fp in excl):
                    continue

        # Weak-keyword gate: skip a weak-only match in a wrong-industry lot,
        # keep scanning for a STRONG keyword. If none found → reject (None).
        if (stem in _WEAK_STEMS or kw in _WEAK_STEMS) and has_negative:
            continue

        return kw
    return None


def _escape_md(text: str) -> str:
    """Escape Markdown special chars for Telegram."""
    for ch in ("*", "_", "`", "["):
        text = text.replace(ch, "")
    return text


def _lookup_tender_uuid(external_id: str, source: str) -> Optional[str]:
    """Look up Supabase UUID for a tender by external_id + source."""
    try:
        from crawler.core.feedback import _get_client
        client = _get_client()
        result = (
            client.table("tenders")
            .select("id")
            .eq("external_id", external_id)
            .eq("source", source)
            .limit(1)
            .execute()
        )
        if result.data:
            return result.data[0]["id"]
    except Exception:
        pass
    return None


# Detail page base URL
_DETAIL_PAGE_BASE = "https://parsing-seo.vercel.app/tenders"


def _format_alert(
    tender: RawTender,
    matched_kw: str,
    extra_sources: Optional[List[str]] = None,
    alert_seq: Optional[int] = None,
    db_id: Optional[str] = None,
) -> str:
    """Format a single tender alert message for Telegram."""
    parts = []
    # Alert number + prefix by message type
    prefix = ""
    if alert_seq is not None:
        prefix = "#%03d " % alert_seq
    if tender.message_type == "customer_request":
        parts.append("%s🔥🔥🔥 ГОРЯЧИЙ ЛИД" % prefix)
    elif tender.message_type == "info":
        parts.append("%s[ИНФО]" % prefix)
    else:
        parts.append("%s[ТЕНДЕР]" % prefix if prefix else "")
    parts.append("*%s*" % _escape_md(tender.title[:200]))
    if tender.organization:
        parts.append("Заказчик: %s" % _escape_md(tender.organization))
    if tender.price:
        parts.append("Сумма: %s %s" % ("{:,.0f}".format(tender.price), tender.currency))
    if tender.deadline:
        parts.append("Дедлайн: %s" % tender.deadline)
    # Extra per-source info (region, delivery days, etc.)
    if tender.extra_info:
        for label, value in tender.extra_info.items():
            parts.append("%s: %s" % (_escape_md(label), _escape_md(value)))
    # Show all sources if tender found on multiple platforms
    if extra_sources and len(extra_sources) > 1:
        parts.append("Площадки (%d): %s" % (len(extra_sources), ", ".join(extra_sources)))
    else:
        parts.append("Источник: %s" % tender.source)
    # Link strategy: direct source_url is preferred (one-click to platform).
    # For broken SPA sources the deep-link slips to homepage, so we fall back
    # to our own Vercel detail page (which also gets a screenshot attached).
    # For working sources (ETender UZEX lots, Auctions, etc.) keep the direct
    # link first; add Vercel as a backup so the alert is still browsable if
    # the source goes down.
    from crawler.core.snap import is_broken_spa as _is_broken_spa
    if _is_broken_spa(tender.source):
        # Broken SPA: прямая ссылка в Telegram in-app браузере отрисуется
        # пустой страницей (Angular runtime + auth wall), поэтому шлём только
        # Vercel detail page (со скриншотом через sendPhoto).
        if db_id:
            parts.append("%s/%s" % (_DETAIL_PAGE_BASE, db_id))
        elif tender.source_url:
            parts.append(tender.source_url)
    else:
        # Working source: прямая ссылка первой, Vercel запасной.
        if tender.source_url:
            parts.append(tender.source_url)
        if db_id:
            parts.append("Архив: %s/%s" % (_DETAIL_PAGE_BASE, db_id))

    # Cooperation.uz fallback: SPA detail page often shows "Sahifa topilmadi"
    # in Telegram in-app browser (JavaScript not always executed). Add a
    # search-by-title link on the supplier panel as a safety net.
    if tender.source.startswith("Cooperation.uz") and tender.title:
        try:
            from urllib.parse import quote
            q = quote(tender.title.split()[0])
            parts.append("Поиск: https://new.cooperation.uz/supplier/all?productName=%s" % q)
        except Exception:
            pass

    # «Куда подать КП» — главное для пользователя
    try:
        from crawler.core.snap import is_broken_spa as _is_broken_spa2
        spa_auth = _is_broken_spa2(tender.source) or tender.source.startswith("Cooperation.uz Лоты")
    except Exception:
        spa_auth = False
    submission_lines = []
    if spa_auth and tender.source_url:
        # Deep-link этих источников битый (SPA + auth-wall: прямой URL ведёт на
        # homepage или удалённую/чужую карточку — напр. prequest id попадает в
        # e-shop /home/shop/detail и резолвится в чужой товар). НЕ показываем
        # сырой source_url — он дезориентирует ("кликнул печать → увидел адаптер
        # 2021"). Карточка с реальными данными уже идёт Vercel-ссылкой выше;
        # здесь даём рабочий КОРЕНЬ площадки + инструкцию найти лот по названию.
        try:
            from urllib.parse import urlparse
            _p = urlparse(tender.source_url)
            _home = "%s://%s" % (_p.scheme, _p.netloc) if (_p.scheme and _p.netloc) else tender.source_url
        except Exception:
            _home = tender.source_url
        # Prefer the unique public lot number (displayId) as the search key —
        # generic category titles ("Услуги издательские") match hundreds of lots,
        # the number is exact. UZEX prequest has no public /procedure-style deep
        # link (SPA behind E-IMZO; verified 2026-06-08), so number-search is the
        # best handoff; full data + screenshot are on the Vercel page above.
        _disp = ""
        if isinstance(tender.extra_info, dict):
            _disp = str(tender.extra_info.get("display_id") or "").strip()
        if _disp:
            submission_lines.append("📝 Подача КП: %s (E-IMZO) — поиск по номеру лота %s" % (_home, _disp))
        else:
            submission_lines.append("📝 Подача КП: %s (E-IMZO) — найти по названию выше" % _home)
    extra = tender.extra_info if isinstance(tender.extra_info, dict) else {}
    contacts = extra.get("customer_contacts") if extra else None
    if isinstance(contacts, dict):
        if contacts.get("email"):
            submission_lines.append("✉️ Email заказчика: %s" % contacts["email"])
        if contacts.get("phone"):
            submission_lines.append("📞 Тел: %s" % contacts["phone"])
    if submission_lines:
        parts.append("")
        parts.extend(submission_lines)

    parts.append("#%s" % matched_kw.replace(" ", "_"))
    return "\n".join(parts)


async def send_alerts(
    new_tenders: List[RawTender],
    dry_run: bool = False,
    group_sources: Optional[Dict[str, List[str]]] = None,
) -> int:
    """Filter tenders by keywords and send Telegram alerts.

    Args:
        new_tenders: List of tenders that were newly inserted (not seen before).
        dry_run: If True, log but don't send.
        group_sources: Optional dict {tender.id: [source_names]} for cross-source groups.

    Returns:
        Number of alerts sent.
    """
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        logger.debug("[Alerts] Bot token or chat ID not set, skipping alerts")
        return 0

    keywords = _get_keywords()
    if not keywords:
        logger.debug("[Alerts] No keywords configured, skipping")
        return 0

    # Filter out competitor ads (info) — only alert on tenders and customer requests
    ALERT_TYPES = ("tender", "customer_request")
    relevant = [t for t in new_tenders if t.message_type in ALERT_TYPES]
    info_count = len(new_tenders) - len(relevant)
    if info_count:
        logger.info("[Alerts] Skipped %d info/ads (not tender or customer_request)", info_count)

    # Filter out tenders below minimum price (10M UZS)
    MIN_PRICE = 10_000_000
    priced = [t for t in relevant if t.price is None or t.price >= MIN_PRICE]
    low_price_count = len(new_tenders) - len(priced)
    if low_price_count:
        logger.info("[Alerts] Skipped %d tenders below %dM price threshold", low_price_count, MIN_PRICE // 1_000_000)

    # Filter out tenders with expired deadlines
    active = [t for t in priced if not _is_deadline_expired(t)]
    expired_count = len(priced) - len(active)
    if expired_count:
        logger.info("[Alerts] Skipped %d tenders with expired deadlines", expired_count)

    # Stale-tender filter: drop anything with deadline >365 days in the past.
    # Catches Hayotbirja тендеры (2020-12-28) and Xarid Конкурсы (2022-07-13)
    # — adapter regression where parser keeps returning archived rows.
    # _parse_deadline returns naive datetime, so compare with naive utcnow.
    _STALE_CUTOFF = datetime.utcnow() - timedelta(days=365)
    fresh = []
    stale_count = 0
    for t in active:
        dt = _parse_deadline(t.deadline)
        if dt is None:
            fresh.append(t)
            continue
        # Strip tzinfo if present so the comparison is always naive vs naive.
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        if dt >= _STALE_CUTOFF:
            fresh.append(t)
        else:
            stale_count += 1
    if stale_count:
        logger.info("[Alerts] Skipped %d stale tenders (deadline >1 year past)", stale_count)
    active = fresh

    # Filter matching tenders by keywords
    matching = []  # type: List[Tuple[RawTender, str]]
    for t in active:
        kw = _find_matching_keyword(t, keywords)
        if kw:
            matching.append((t, kw))

    if not matching:
        logger.info("[Alerts] No tenders match alert keywords (%d checked)", len(new_tenders))
        return 0

    logger.info("[Alerts] %d tenders match keywords (out of %d new)", len(matching), len(new_tenders))

    # Fast reject filter — remove obvious non-relevant items before AI.
    # Note: "книги печатные" was here but removed 2026-04-19 because it is the
    # OKED category name in UZEX prequest/etender — collapses entire UZEX feed
    # to 0 alerts. Let AI decide on a per-item basis.
    _REJECT_TITLES = [
        "подписке и доставке периодического печатного издания",
        "подписке и доставке периодических печатных изданий",
        "марля полиграфическая",
        "nfc визитк",
    ]
    before_reject = len(matching)
    matching = [
        (t, kw) for t, kw in matching
        if not any(rej in t.title.lower() for rej in _REJECT_TITLES)
    ]
    rejected_fast = before_reject - len(matching)
    if rejected_fast:
        logger.info("[Fast Reject] Removed %d non-relevant tenders by title", rejected_fast)

    # UZEX prequest fast-pass: titles are OKED category names ("Услуги печатные...",
    # "Книги печатные") so AI conservatively rejects them. For UZEX-family sources,
    # if the title obviously matches our niche by category we skip the AI cost
    # and let it through — Daniyar wants prequals to come "так успеем подготовиться".
    _UZEX_PASSTHROUGH_SOURCES = {
        "UZEX Предквалификации",
        "UZEX Результаты",
        "ETender UZEX",
        "ETender Обсуждения",
    }
    _UZEX_NICHE_HINTS = (
        "печатн", "полиграф", "упаков", "пакет", "коробк",
        "этикет", "брошюр", "буклет", "стикер", "календар",
        "блокнот", "конверт", "сувенир", "ежедневник", "обложк", "bosma",
    )
    uzex_bypass: List[Tuple[RawTender, str]] = []
    rest: List[Tuple[RawTender, str]] = []
    for t, kw in matching:
        title_l = (t.title or "").lower()
        if (
            t.source in _UZEX_PASSTHROUGH_SOURCES
            and any(hint in title_l for hint in _UZEX_NICHE_HINTS)
        ):
            uzex_bypass.append((t, kw))
        else:
            rest.append((t, kw))
    if uzex_bypass:
        logger.info("[UZEX Pass] %d UZEX-family tenders bypass AI by category match", len(uzex_bypass))
    matching = rest

    if not matching and not uzex_bypass:
        logger.info("[Alerts] All tenders rejected by fast filter")
        return 0

    if dry_run:
        for i, (t, kw) in enumerate(matching + uzex_bypass):
            logger.info("[Alerts] DRY RUN would send: #%03d [%s] %s", i + 1, kw, t.title[:80])
        return len(matching) + len(uzex_bypass)

    # AI relevance filter — reject false positives via Qwen (parallel).
    # Side effects: writes score/category/reason back onto each tender (for
    # downstream DB persistence) and best-effort UPDATE existing rows in DB.
    if settings.openrouter_api_key:
        import asyncio as _asyncio
        from crawler.core.db import update_relevance_fields

        async with httpx.AsyncClient(timeout=15) as ai_client:
            sem = _asyncio.Semaphore(5)

            async def _check(tender_kw):
                # type: (Tuple[RawTender, str]) -> Tuple[RawTender, str, RelevanceResult]
                tender, kw = tender_kw
                # Customer requests (group leads) are already vetted by the
                # demand-vs-ad classifier + keyword prefilter. The tender
                # product-scope prompt misjudges colloquial leads (a bag demand
                # "сумка 1250шт, кимда бор" scored 0/irrelevant), so skip it for
                # them — never drop a hot lead. Tenders are still checked.
                if getattr(tender, "message_type", None) == "customer_request":
                    return tender, kw, _allow("customer_request lead: skip product-scope")
                async with sem:
                    result = await _ai_check_relevance(tender, ai_client)
                # Mutate tender so any later code (DB update, formatter) sees the score.
                if result.score is not None:
                    tender.relevance_score = result.score
                    tender.relevance_category = result.category
                    tender.relevance_reason = result.reason
                return tender, kw, result

            scored = await _asyncio.gather(*[_check(tk) for tk in matching])

        # Persist score back to DB (fire-and-forget — failures are logged but
        # don't block alerting). Skip when score is None (AI fallback).
        for tender, _kw, result in scored:
            if result.score is not None:
                update_relevance_fields(
                    tender.external_id,
                    tender.source,
                    result.score,
                    result.category or "",
                    result.reason or "",
                )

        filtered = [(t, kw) for t, kw, r in scored if r.is_relevant]  # type: List[Tuple[RawTender, str]]
        rejected = len(matching) - len(filtered)
        if rejected:
            logger.info("[AI Filter] Passed %d / %d (rejected %d)", len(filtered), len(matching), rejected)
        matching = filtered

        # Annotate-not-gate: UZEX-passthrough items are SENT regardless (by
        # design — Daniyar wants prequalifications early), but we still score
        # them with the cheap fast model for coverage/data (this stream had 0%
        # AI coverage). Score is persisted; the item is NEVER dropped here.
        if uzex_bypass:
            fast_only = settings.ai_relevance_model_fast or settings.ai_relevance_model
            try:
                async with httpx.AsyncClient(timeout=20) as ann_client:
                    sem_a = _asyncio.Semaphore(5)

                    async def _annotate(tk):
                        t, _kw = tk
                        async with sem_a:
                            r = await _ai_call_one(t, ann_client, fast_only, role="fast")
                        if r is not None and r.score is not None:
                            t.relevance_score = r.score
                            t.relevance_category = r.category
                            t.relevance_reason = r.reason
                            update_relevance_fields(t.external_id, t.source, r.score, r.category or "", r.reason or "")
                        return None

                    await _asyncio.gather(*[_annotate(tk) for tk in uzex_bypass])
                logger.info("[AI Annotate] Scored %d UZEX-passthrough items (sent regardless)", len(uzex_bypass))
            except Exception as exc:
                logger.warning("[AI Annotate] failed (non-fatal): %s", str(exc)[:120])

        if not matching and not uzex_bypass:
            logger.info("[Alerts] All tenders rejected by AI filter")
            return 0

    # Merge UZEX-bypass items back in (they skipped AI by design)
    if uzex_bypass:
        matching = matching + uzex_bypass

    # Hot leads first: customer_request items (real clients asking to buy NOW)
    # jump the queue — lowest seq + sent before tender noise. Stable sort keeps
    # relative order within each group.
    matching.sort(key=lambda tk: 0 if getattr(tk[0], "message_type", None) == "customer_request" else 1)

    # Reserve sequential alert numbers
    from crawler.core.feedback import get_next_seq, save_alert_seq
    start_seq = get_next_seq(len(matching))

    # Send via Telegram Bot API (no Telethon needed — just HTTP)
    bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    sent = 0

    _group_sources = group_sources or {}

    # Broken SPA sources: take a screenshot of OUR own /tenders/{uuid} page
    # and attach it to the alert as a photo. The platform's deep-link is
    # useless (slips back to homepage) so a visual preview of our UI card
    # is the most informative thing we can show in Telegram.
    from crawler.core.snap import is_broken_spa, snap_and_upload

    photo_send_url = "https://api.telegram.org/bot%s/sendPhoto" % settings.telegram_bot_token

    async with httpx.AsyncClient(timeout=30) as client:
        for i, (tender, kw) in enumerate(matching):
            seq = start_seq + i
            extra = _group_sources.get(tender.id)
            # Look up Supabase UUID for detail page link
            db_id = _lookup_tender_uuid(tender.external_id, tender.source)
            text = _format_alert(tender, kw, extra_sources=extra, alert_seq=seq, db_id=db_id)
            # Inline keyboard for feedback
            reply_markup = {
                "inline_keyboard": [[
                    {"text": "\U0001f464 \u041a\u043b\u0438\u0435\u043d\u0442", "callback_data": "fb:%d:ok" % seq},
                    {"text": "\U0001f4e2 \u0420\u0435\u043a\u043b\u0430\u043c\u0430", "callback_data": "fb:%d:ad" % seq},
                    {"text": "\u274c \u041c\u0438\u043c\u043e", "callback_data": "fb:%d:skip" % seq},
                ]]
            }

            # Attempt to capture our /tenders/{uuid} page if the source is a broken SPA
            photo_url = None
            if db_id and is_broken_spa(tender.source):
                try:
                    photo_url = await snap_and_upload(db_id, tender.source, tender.external_id)
                    if photo_url:
                        # Persist URL so the frontend / repair flow can reuse it
                        try:
                            from crawler.core.feedback import _get_client as _gc
                            ei = dict(tender.extra_info or {})
                            ei["screenshot_url"] = photo_url
                            ei["screenshot_at"] = datetime.now(timezone.utc).isoformat()
                            _gc().table("tenders").update({"extra_info": ei}).eq("id", db_id).execute()
                        except Exception as exc:
                            logger.warning("[Alerts] save screenshot extra_info failed: %s", str(exc)[:200])
                except Exception as exc:
                    logger.warning("[Alerts] snap failed for #%d: %s", seq, str(exc)[:200])

            try:
                if photo_url:
                    # sendPhoto \u2014 caption max 1024 chars
                    caption = text if len(text) <= 1024 else (text[:1020] + "...")
                    resp = await client.post(photo_send_url, json={
                        "chat_id": settings.telegram_alert_chat_id,
                        "photo": photo_url,
                        "caption": caption,
                        "parse_mode": "Markdown",
                        "protect_content": True,
                        "reply_markup": reply_markup,
                    })
                else:
                    resp = await client.post(bot_url, json={
                        "chat_id": settings.telegram_alert_chat_id,
                        "text": text,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                        "protect_content": True,
                        "reply_markup": reply_markup,
                    })
                if resp.status_code == 200:
                    sent += 1
                    # Save alert_seq and telegram_message_id
                    resp_data = resp.json()
                    tg_msg_id = None
                    if resp_data.get("ok") and resp_data.get("result"):
                        tg_msg_id = resp_data["result"].get("message_id")
                    save_alert_seq(tender.external_id, tender.source, seq, tg_msg_id)
                else:
                    logger.warning(
                        "[Alerts] Failed to send alert #%d: %d %s",
                        seq, resp.status_code, resp.text[:200],
                    )
            except Exception as exc:
                logger.warning("[Alerts] Error sending alert #%d: %s", seq, str(exc))

    logger.info("[Alerts] Sent %d / %d alerts (seq #%d-#%d)", sent, len(matching), start_seq, start_seq + len(matching) - 1)
    return sent


async def send_healthcheck(
    stats: dict,
    new_count: int,
    alerts_sent: int,
    errors: List[str] = None,
) -> None:
    """Send crawl summary to Telegram (silent, no notification sound).

    Only sends if there are errors or new tenders. Silent crawls with
    0 new tenders don't generate noise.
    """
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        return

    total = sum(stats.values())
    sources_ok = sum(1 for v in stats.values() if v > 0)
    sources_fail = sum(1 for v in stats.values() if v == 0)

    # Skip quiet runs — no need to spam
    if not errors and new_count == 0:
        return

    parts = []  # type: List[str]
    parts.append("Crawl: %d тендеров (%d источников)" % (total, sources_ok))
    if new_count:
        parts.append("Новых: %d" % new_count)
    if alerts_sent:
        parts.append("Алертов: %d" % alerts_sent)
    if sources_fail:
        failed = [k for k, v in stats.items() if v == 0]
        parts.append("0 items: %s" % ", ".join(failed[:5]))
    if errors:
        parts.append("Ошибки: %s" % "; ".join(errors[:3]))

    text = "\n".join(parts)

    bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(bot_url, json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": text,
                "disable_notification": True,
                "protect_content": True,
            })
    except Exception as exc:
        logger.warning("[Healthcheck] Failed to send: %s", str(exc)[:80])


async def send_quality_alert(report, dry_run=False):
    # type: (Any, bool) -> None
    """Send critical quality regression alert to Telegram."""
    if not settings.telegram_bot_token or not settings.telegram_alert_chat_id:
        return

    text = "Quality regression detected!\n\n%s" % report.summary()
    if len(text) > 3900:
        text = text[:3900] + "..."

    if dry_run:
        logger.info("[Quality Alert] DRY RUN: %s", text[:200])
        return

    bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(bot_url, json={
                "chat_id": settings.telegram_alert_chat_id,
                "text": text,
                "disable_notification": False,
            })
    except Exception as exc:
        logger.warning("[Quality Alert] Failed to send: %s", str(exc)[:80])
