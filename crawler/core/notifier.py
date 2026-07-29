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

# Borderline-reject escalation gate (p95 fix, 2026-06-22). The slow Max model is
# only a second opinion on fast REJECTS, to rescue ~4% false-rejects which are
# "смежная" (40-69) cases. A confident reject (< 40 = "точно не наш" per the
# prompt) is a reliable true negative and does NOT need Max — skipping it cuts the
# slow Max calls that dominate AI p95 (Max ≈ the 15s client timeout). Mirrors the
# validated cooperation gate (77d9897).
_MAX_ESCALATE_MIN_SCORE = 40


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
{playbook}{source_context}
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


# Лёгкий фильтр СПАМА для TG-лидов (customer_request). НЕ product-scope-промпт:
# тот зарезал разговорные заказы («сумка 1250шт» → 0). Этот отсекает только то,
# что вообще НЕ заказ-на-изготовление, плюс категории вне профиля Winch,
# подтверждённые Данияром 12.06 (пошив текстиля, резка/наружные конструкции).
# Поток TG PR Media Group был ~30% мусора: реклама-самопиар (Eco Print ×10,
# KOMRON PRESS), вакансии (usta/meneger kerak), техника (моноблок, блок питания).
#
# Расширен 28.07 по замеру на бенчмарке: изолированно гейт держал precision 30%
# (6 из 20 лидов, помеченных Данияром как шум, всё равно доходили). Добавлены
# четыре различения, каждое — граница, а не список слов, потому что примеры по
# обе стороны выглядят почти одинаково:
#   лист SR3 320х450 бумага 300г = НАШЕ  ↔  лист 1220х2440 композит = широкоформат
#   готовые пакеты без логотипа  = НАШЕ  ↔  рулонная самоклейка = сырьё коллеги
#   печать на готовом изделии    = НАШЕ  ↔  изготовление изделия из силикона
#   печать каталога              = НАШЕ  ↔  дизайнер для каталога (услуга без печати)
# Результат замера: precision 30% → 85%, recall 12/12 БЕЗ изменений (проверено
# двумя одинаковыми прогонами). Fail-open остаётся полом — правило не должно
# уметь дропнуть живой заказ.
#
# Пятая граница добавлена 29.07 — плёнка (единственный оставшийся промах
# бенчмарка, c0046 «УФ-печать на прозрачной плёнке с белым цветом»). Он не был
# стабильным: fast говорил keep 5/5, pro сама себе противоречила 3 drop / 2 keep
# на одном тексте — отсюда и плавающие ±0.1 в скоре версии. Причина не в модели,
# а в промпте: «плёнка» лежала внутри широкоформатного буллета, а UV был назван
# нашим в keep-списке, и зацепиться можно было за любое.
#   печать наклеек, в т.ч. на прозрачной самоклейке = НАШЕ (готовая продукция)
#   УФ-печать по коробке/папке/бокалу              = НАШЕ (печать по изделию)
#   УФ-печать по прозрачной плёнке, «белым цветом» = НЕ наше (белил нет)
# Два отвергнутых варианта — почему формулировка именно такая:
#   • шапка «решает МАТЕРИАЛ, а не технология» сдвинула fast-модель на «материал
#     не назван → drop» и срезала 7 живых лидов из 104, включая выставочные
#     стенды и фирменный пакет с коробкой — прямо из keep-списка;
#   • формулировка «печать по плёнке — прозрачной, самоклеящейся» проглатывала
#     печать прозрачных наклеек, поэтому исключение про наклейки стоит прямо
#     внутри буллета, а «КОРОБКА» продублирована в keep — без этого якоря
#     «UV pechat … qutuni ustiga» начинал плавать 1/5.
# Замер итоговой версии: цель 5/5 drop, 32/32 корпус-лида верно и без плавания,
# на 104 живых лидах единственное расхождение с продом (#2720, сборный подарок:
# ляган + подставки + пакет + коробка) плавает и на самом проде 3/5 — это его
# собственный давний случай, не следствие этой правки.
#
# Шестая граница — СМЕШАННАЯ КОРЗИНА (29.07), тот самый #2720 и его класс.
# Замер на 20 размеченных заявках показал, что это не единичный случай: гейт
# терял 5 из 8 смешанных заявок, цепляясь за ПЕРВЫЙ чужой пункт и не замечая
# нашего («пошив футболки с нанесением логотипа» → drop с причиной «пошив как
# изделие», хотя нанесение — ровно наша работа).
#   пошив футболки + НАНЕСЕНИЕ ЛОГО   = keep (наша часть — печать)
#   ляган + пакет + коробка           = keep (наша часть — упаковка)
#   коробка РАСПРЕДЕЛИТЕЛЬНАЯ в списке электрики = drop (омоним, не упаковка)
# Правило намеренно стоит в keep-списке, а не шапкой сверху: так оно способно
# только ДОБАВИТЬ keep и не может создать новых drop — прошлая правка показала,
# что глобальная шапка сдвигает поведение fast-модели на всём потоке. Исключение
# про омонимы стоит внутри буллета, иначе перечни снабжения («короб кабельный»,
# «краски полиграфические», «карандаши») начали бы проходить.
# Замер: 5 промахов → 3, все 12 контрольных перечней-омонимов держатся 3/3,
# 32/32 лида корпуса без изменений.
#
# Седьмая граница — МЕРЧ ИЗ НЕ-БУМАЖНЫХ МАТЕРИАЛОВ (29.07). Из трёх оставшихся
# промахов два были не логикой, а вопросом профиля: берём ли заявку, где предмет
# делает подрядчик, а наше — только нанесение. Данияр ответил «берём», и это
# развело два правила, которые до сих пор противоречили друг другу на одних и
# тех же заявках (drop «изготовление из не-бумажных материалов» против keep
# «мерч с лого»). Теперь ось не материал, а НАЛИЧИЕ НАНЕСЕНИЯ:
#   деревянный бейджик, кубок с гравировкой, надстаканник с лого = keep
#   «футболка кепка керак» без логотипа                          = drop (закупка)
#   силиконовый браслет, резиновый логотип                       = drop (литьё)
# Литьё/формовка остались чужими намеренно: метки на них ставил сам Данияр
# (корпус c0066, c0042), и правка их не сдвинула — проверено отдельно.
# Условие «нанесение должно быть в самом запросе» появилось не сразу: без него
# правило подхватывало «футболка кепка керак» 3 из 5 раз. Замер финальной
# версии: 1 промах из 23 (это «папка из картона» — другой вопрос, ниже),
# 38/38 лидов корпуса, на 104 живых лидах ровно 2 расхождения с продом и оба
# ожидаемые — именная табличка с гравировкой и брендированные кожаные
# салфетницы.
#
# ОТКРЫТО, решения нет: «закупка готовой канцелярии» — папка из бумаги/картона
# внутри списка скоб, ручек и клея. Правило про поставщика готовой продукции
# нашего профиля говорит keep, здравый смысл про офисную закупку — drop.
# Не кодирую наугад (см. main.md).
_LEAD_SPAM_PROMPT = """Это сообщение из Telegram-чата, где люди ищут исполнителей.
Наша компания — ТИПОГРАФИЯ и УПАКОВОЧНОЕ производство (печать, полиграфия,
упаковка, печать на мерче/ткани, бейджи, выставочные стенды).

Реши: это ЗАКАЗ, который мы можем выполнить, или ШУМ?

ШУМ (intent=drop) — НЕ присылать:
- Реклама/самопиар: компания/человек РЕКЛАМИРУЕТ свои услуги («бизнесингизни…»,
  «премиум сифат», «бизнинг хизматларимиз», прайсы, «sotuvda/sotiladi»)
- Вакансия/поиск работника или контакта: «usta kerak», «ishchi/master/dizayner/
  meneger kerak», «ish bor/kerak», «oylik…», «резюме», «номери кимда бор»
- НЕ-печатная техника/товар: моноблок, компьютер, блок питания, микрофон,
  адаптер, телефон, станок, мебель, продукты, лекарства как товар
- Пошив текстиля как ИЗДЕЛИЯ: сшить форму/футболку/кепку/шарф (НЕ путать с
  ПЕЧАТЬЮ логотипа на готовом текстиле — это НАШЕ)
- ЛИТЬЁ И ФОРМОВКА из силикона, резины, пластика, оргстекла — «сделать
  силиконовый браслет», «резиновый логотип», «объёмная буква». НЕ путать ни с
  ПЕЧАТЬЮ на готовом изделии (визитки на металле, лого на бокале), ни с мерчем
  из дерева/кожи/стекла/металла с нанесением — то и другое НАШЕ, см. keep
- Резка материалов и наружные конструкции: оргстекло, ЛДСП, акрил, лазерная
  резка, неон, стелла, вывеска, монтаж наружной рекламы, брендирование/оклейка
  автомобиля
- ШИРОКОФОРМАТ и печать по листовым/рулонным НЕ-бумажным материалам: баннер,
  бекпринт, люверсы, фотообои, флаги, плёнка, УФ-печать по пластику и композиту
  (лист 1220х2440 и подобные строительные форматы).
  ВАЖНО: полиграфические форматы листа — SRA3/SR3 320х450, А3, А4, бумага и
  картон с граммовкой (170/300 г) — это НАШЕ, не широкоформат
- Печать ПО ПРОЗРАЧНОЙ ПЛЁНКЕ и печать БЕЛЫМ ЦВЕТОМ (белилами): «УФ-печать на
  прозрачной плёнке», «с белым цветом», печать по ПВХ-плёнке — белил у нас нет,
  это не наше оборудование. Исключения — это НАШЕ: наклейки и стикеры (в т.ч.
  прозрачные самоклеящиеся); УФ-печать ПО ГОТОВОМУ ИЗДЕЛИЮ — по коробке, папке,
  бокалу, визитке
- СЫРЬЁ и расходники для печати: рулонная самоклейка, плёнка в рулонах, бумага
  в рулонах, краска, пластины — так закупается типография-коллега, а не наш
  заказчик. Готовая продукция нашего профиля (пакеты, коробки) — наоборот, keep
- Услуга БЕЗ печати: нужен дизайнер, макет, вёрстка, дизайн-файл, фотограф

ЗАКАЗ (intent=keep) — присылать:
- Печать/полиграфия: наклейки, штрихкоды, флаеры, А4/А5/А6, блокноты, каталоги,
  бланки, визитки, открытки, календари, альбомы, офсет, цифровая печать листов
- Упаковка: коробки, крафт-пакеты, гофра, картон, зип-пакеты, пакеты под маркет-
  плейсы (в т.ч. «готовые пакеты без логотипа» — это наш товар)
- Печать ЛОГО на готовом: футболка/кепка/флаг/бокал/тарелка/КОРОБКА с логотипом
  (DTF/сублимация/UV) — товар уже есть, нужна печать по нему
- Бейджи, ланьярды, стенды выставочные, таблички, мерч (ручки/браслеты с лого)
- МЕРЧ И КОРПОРАТИВНЫЕ ПОДАРКИ из дерева, кожи, стекла, металла — когда на
  предмете есть НАНЕСЕНИЕ (логотип, гравировка, тиснение, брендирование):
  деревянный бейджик, надстаканник/капхолдер, кубок с гравировкой, медаль,
  брендированный стакан, ежедневник в коже. Предмет делает подрядчик,
  брендирование наше — заявку присылать.
  НАНЕСЕНИЕ должно быть в самом запросе. «Футболка кепка керак», «нужны
  стаканы» без логотипа — это закупка товара, а не наш заказ: drop.
  Литьё и формовка тоже остаются чужими: силиконовый браслет, резиновый
  логотип, объёмная буква — там нет ни печати, ни брендирования предмета
- СМЕШАННАЯ ЗАЯВКА: в запросе есть И наша позиция, И чужая — это KEEP, мы
  возьмём свою часть. «Пошив футболки С НАНЕСЕНИЕМ ЛОГОТИПА» — нанесение наше;
  «ляган + фирменный пакет + дизайнерская коробка» — пакет и коробка наши;
  «кожаные стаканы + брендированные капхолдеры» — брендирование наше. Решает
  НАЛИЧИЕ нашей позиции, а не то, что чужих пунктов больше.
  НО: если «наше» слово попало в перечень случайно и означает другое — коробка
  РАСПРЕДЕЛИТЕЛЬНАЯ и короб кабельный (электрика), плёнка и самоклейка в
  рулонах (сырьё), краски и смывка для печати (расходники типографии),
  карандаши и скобы (канцтовар) — это по-прежнему drop
- Поиск ПОСТАВЩИКА готовой продукции нашего профиля (пакеты, коробки, сумки,
  мерч) — это потенциальный клиент, keep

Сообщение:
{text}

Ответь СТРОГО JSON (только JSON): {{"intent": "keep|drop", "reason": "<до 80 симв>"}}
/no_think"""


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
    # Э-магазин = объявления ПОСТАВЩИКОВ (предложение товара), не запрос
    # покупателя. Без этого контекста AI фантазирует связи («печать этикеток
    # для ампул» на объявление лекарства Epinephrine, score 95 — FP первого
    # краула 2026-06-10) и триггерится на «ИД упаковки» в фарм-описаниях.
    _src_note = ""
    if "э-магазин" in (tender.source or "").lower():
        _src_note = (
            "\nКОНТЕКСТ ИСТОЧНИКА: это ОБЪЯВЛЕНИЕ ПОСТАВЩИКА в э-магазине "
            "(предложение товара), а не запрос покупателя. Оцени САМ ТОВАР: "
            "наш ТОЛЬКО если товар — полиграфическая/бумажно-картонная "
            "продукция или выставочные стенды/таблички/бейджи, т.е. то, что "
            "типография могла бы изготовить и предложить таким же объявлением. "
            "Лекарства, техника, продукты питания, растения, одежда = "
            "irrelevant (score 0-20), ДАЖЕ если в характеристиках товара "
            "упоминается упаковка/этикетка («ИД упаковки», «ампулы №10 в "
            "упаковке») — упаковка чужого товара != заказ на печать.\n"
        )
    prompt = _RELEVANCE_PROMPT.format(
        title=tender.title[:300],
        organization=tender.organization or "",
        details=_details,
        playbook=_pb_block,
        source_context=_src_note,
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


async def _ai_lead_is_spam(
    tender: RawTender,
    client: httpx.AsyncClient,
) -> bool:
    """Lightweight spam gate for TG leads. True = drop (spam/out-of-scope).

    Fail-open: on any network/parse error returns False (keep the lead) — we
    never silently drop a hot lead because the AI hiccuped.
    """
    if not settings.openrouter_api_key:
        return False
    text = (tender.search_text or tender.title or "")[:1000]
    prompt = _LEAD_SPAM_PROMPT.format(text=text)
    fast_model = settings.ai_relevance_model_fast or settings.ai_relevance_model
    pro_model = settings.ai_relevance_model
    import asyncio as _aio

    async def _intent(model):
        # type: (str) -> Optional[str]
        """One model's verdict: 'drop'/'keep', or None on persistent failure.
        Retries transient provider errors before giving up."""
        for attempt in range(3):
            try:
                resp = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": "Bearer %s" % settings.openrouter_api_key},
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 200,
                        "temperature": 0,
                        "reasoning": {"enabled": False},
                        "response_format": {"type": "json_object"},
                    },
                )
                resp.raise_for_status()
                payload = _extract_json_object(_strip_think_tags(
                    resp.json()["choices"][0]["message"]["content"]))
                if payload:
                    return str(payload.get("intent", "")).lower().strip()
            except Exception:
                pass
            if attempt < 2:
                await _aio.sleep(0.5 * (attempt + 1))
        return None

    # Hybrid fast -> pro. The fast model is TEMPORALLY inconsistent on borderline
    # self-promo («KOMRON PRESS», «Eco Print … PREMIUM SIFAT»): it dropped them in
    # tests yet KEPT them on the 06-29/30 crawls (provider routing variance — not a
    # code error: AI err 0%, no fail-open). So a fast non-drop is double-checked by
    # the stronger pro model (which dropped them 3/3). Mirrors the tender hybrid
    # (there pro catches fast's false-REJECTS; here pro catches fast's false-KEEPS).
    # Fail-open (keep) stays the floor — never drop a real lead on an AI hiccup.
    if await _intent(fast_model) == "drop":
        logger.info("[Lead Spam] dropped (fast): %s", tender.title[:50])
        return True
    if await _intent(pro_model) == "drop":
        logger.info("[Lead Spam] dropped (pro 2nd-opinion): %s", tender.title[:50])
        return True
    return False


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

    # Confident reject (fast score < 40 = "точно не наш") → trust it, skip the
    # slow Max call. The Max rescue is for BORDERLINE rejects ("смежная", 40-69),
    # where false-rejects live — not clear-cut ones. This is the p95 lever.
    if (fast_result.score or 0) < _MAX_ESCALATE_MIN_SCORE:
        logger.info(
            "[AI Filter:fast] REJECTED (confident, skip max) score=%d cat=%s: %s",
            fast_result.score, fast_result.category, tender.title[:60],
        )
        return fast_result

    # Borderline fast reject → second opinion from Max (catches "печатные…
    # копированию звуко-видеозаписей" tenders that a3b false-rejects ~4% of time).
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


def _is_deadline_expired(tender: RawTender, now: Optional[datetime] = None) -> bool:
    """Check if tender deadline has already passed. Returns False if no deadline.

    ``now`` lets replay/benchmark evaluate a HISTORICAL tender as of its own day
    (default None = wall clock, i.e. exactly the old behavior). Naive UTC; an
    aware value is normalized.
    """
    dt = _parse_deadline(tender.deadline)
    if dt is None:
        return False  # no deadline or unparseable = let it through
    if now is None:
        ref = datetime.now(timezone.utc).replace(tzinfo=None)
    elif now.tzinfo is not None:
        ref = now.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        ref = now
    return dt < ref - timedelta(days=1)  # 1 day grace period


# Minimum stem length for fuzzy matching (Russian word roots)
_MIN_STEM = 4


def _get_keywords() -> List[str]:
    """Parse comma-separated keywords from settings."""
    raw = settings.alert_keywords or ""
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


def _load_tnved_scope() -> List[str]:
    """Promoted ТНВЭД code prefixes (via shadow_search --promote). Empty by default."""
    try:
        from crawler.core.db import _get_client
        row = (_get_client().table("crawler_settings").select("value")
               .eq("key", "tnved_scope").limit(1).execute().data or [])
        raw = (row[0].get("value") or "") if row else ""
        return [p.strip() for p in raw.split(",") if p.strip()]
    except Exception:
        return []


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

# Reverse (counter) auctions — buyer posts demand, suppliers bid the price DOWN.
# Labeled distinctly in alerts so the bidding dynamic is obvious. Names must match
# sources.yaml exactly (verified 2026-06-21). Generic "Аукционы" feeds are excluded
# (direction ambiguous — may be forward/selling).
_REVERSE_AUCTION_SOURCES = {
    "UZEX Обратные аукционы",
    "Hayotbirja встречные аукционы",
    "XT-Xarid встречные аукционы",
    "E-Birja встречные аукционы",
    "E-Birja встречный аукцион (листинг)",
    "Cooperation.uz Аукционы",  # buyer reverse auctions (coop unification 2026-07-22)
}

# Sell-side catalog markers: a supplier LISTING goods is not demand — these sources
# never earn a push regardless of price (asking price ≠ buyer). "э-магазин" closed the
# e-shop leak 2026-07-03; "оферт" covers Cooperation.uz Оферты, which reaches the
# shared pipeline with the coop unification (2026-07-22) and would otherwise push via
# the big-ticket override. Digest-only — deliberately NOT in _NO_PUSH_SOURCES (that
# drops items entirely; we want digest visibility).
_SUPPLIER_CATALOG_MARKERS = ("э-магазин", "оферт")

# Supplier-catalog / off-profile sources dropped from PUSH (deep-think 2026-07-01):
# e-shops are sell-side (sellers list offers) — no buyer, no demand signal (bid field
# absent from the API). They were 26%+ of alerts, incl. Winch's OWN lots. Still crawled
# and stored (visible on Vercel); Phase-2 surfaces the демандные ones via a digest.
_NO_PUSH_SOURCES = {
    "XT-Xarid э-магазин",              # pure supplier catalog (twin hayotbirja-shop already off)
    "UZEX Э-магазин рекламные услуги",  # off-profile (ads placement, not our print)
}

# Winch's own org/vendor strings — never alert (a self-alert for an already-won lot
# erodes trust). Normalized substring match. Bare surname EXCLUDED (collision risk);
# only the full ЧП legal form.
_OWN_ORG_FRAGMENTS = frozenset({
    "winch", "винч", "салахутдинов д.у", "salakhutdinov d.u",
})


def _is_own_lot(org: Optional[str]) -> bool:
    """True if the organization is Winch's own (suppress — it's our posting)."""
    if not org:
        return False
    norm = " ".join(org.casefold().split())
    return any(frag in norm for frag in _OWN_ORG_FRAGMENTS)


# ── Prefilter: stages 2-11 of send_alerts as a pure, replayable function ─────
#
# Extracted 2026-07-27 so that replay/benchmark tooling runs THE SAME code the
# production pipeline runs — not a hand-maintained copy that silently drifts.
# send_alerts() delegates here; behavior and log lines are byte-identical to the
# pre-refactor inline version (the A/B dry-run diff on prod data was empty).
# These constants used to be locals inside send_alerts — module level so replay
# and version_scorecard can see (and version) them.

# Filter out competitor ads (info) — only alert on tenders and customer requests
_ALERT_TYPES = ("tender", "customer_request")

# Minimum lot value. Lowered 10M → 5M UZS on 2026-07-26 by Daniyar's call, on
# measurement rather than taste: over 30 days, 165 lots whose titles are core
# profile («Услуга типографий», «Услуги издательские», «Услуги печатные») were
# dropped here and NEVER reached the AI — the price gate fires before it. Scoring
# a random 40 of them through the live classifier returned 37 × client @ 90-100
# and only 3 irrelevant, i.e. ~5 real print orders/day were invisible purely for
# being cheap. 5M keeps the biggest bucket (5-10M ≈ 56 lots/30d) while still
# cutting the sub-1M dust. Raising it back is a one-line change.
MIN_PRICE = 5_000_000

# Stale-tender filter horizon: drop anything with deadline >365 days in the past.
# Catches Hayotbirja тендеры (2020-12-28) and Xarid Конкурсы (2022-07-13)
# — adapter regression where parser keeps returning archived rows.
# NOTE (pinned by test_prefilter_parity): this stage is currently unreachable —
# both it and _is_deadline_expired parse the same `deadline` field, and the
# expired gate (1-day grace) always fires first. Kept for parity, not "fixed".
STALE_DAYS = 365

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


class DropStage(object):
    """Canonical stage names — the single vocabulary for replay/audit/scorecard."""

    MESSAGE_TYPE = "message_type"
    NO_PUSH_SOURCE = "no_push_source"
    OWN_LOT = "own_lot"
    MIN_PRICE = "min_price"
    DEADLINE_EXPIRED = "deadline_expired"
    STALE = "stale"
    NO_KEYWORD = "no_keyword"
    REJECT_TITLE = "reject_title"
    ORDER = (MESSAGE_TYPE, NO_PUSH_SOURCE, OWN_LOT, MIN_PRICE,
             DEADLINE_EXPIRED, STALE, NO_KEYWORD, REJECT_TITLE)


@dataclass
class PrefilterVerdict:
    """Per-tender outcome of the deterministic stages (2-11)."""

    tender: RawTender
    passed: bool                    # survived all stages — candidate for AI/send
    dropped_at: Optional[str]       # DropStage.* of the FIRST killing stage, or None
    matched_kw: Optional[str]       # keyword or "тнвэд:XXXX"
    uzex_bypass: bool               # True → skips the AI gate (annotate-not-gate)
    is_lead: bool                   # customer_request → AI gate is _ai_lead_is_spam


@dataclass
class PrefilterResult:
    matching: List[Tuple[RawTender, str]]       # AI-gated survivors, prod order
    uzex_bypass: List[Tuple[RawTender, str]]    # AI-bypass survivors, prod order
    verdicts: List[PrefilterVerdict]            # 1:1 with the input, input order
    counters: Dict[str, int]                    # DropStage.* -> dropped; + passed/bypass


def prefilter(
    new_tenders: List[RawTender],
    keywords: List[str],
    tnved_scope: Optional[List[str]] = None,
    now: Optional[datetime] = None,
    tnved_scope_loader=None,
) -> PrefilterResult:
    """Deterministic filter stages of the alert pipeline, side-effect-free.

    Pure by contract: no DB, no HTTP, no settings reads — keywords and
    tnved_scope are injected by the caller (send_alerts passes the live ones,
    replay passes whatever era it is reconstructing). ``now`` anchors the
    deadline/stale stages so a historical tender can be judged as of its day.

    ``tnved_scope_loader`` exists ONLY to preserve the exact prod log stream:
    the scope SELECT historically ran between the stale and keyword stages, so
    its httpx log line sits at that position in every crawl log (A/B parity
    diff 2026-07-27 caught the move). send_alerts passes the loader; replay
    passes an explicit ``tnved_scope`` list and stays offline.

    Log lines are intentionally byte-identical to the pre-2026-07-27 inline
    code — including the known quirk that the "below price threshold" counter
    is cumulative from the ORIGINAL input, not per-stage. Prod-log diffing
    relies on this; do not "fix" the wording here.
    """
    total_input = len(new_tenders)
    verdicts = [
        PrefilterVerdict(
            tender=t, passed=False, dropped_at=None, matched_kw=None,
            uzex_bypass=False, is_lead=(t.message_type == "customer_request"),
        )
        for t in new_tenders
    ]

    def _result(matching_idx, bypass_idx):
        # type: (List[int], List[int]) -> PrefilterResult
        counters = dict((s, 0) for s in DropStage.ORDER)
        for v in verdicts:
            if v.dropped_at:
                counters[v.dropped_at] += 1
        counters["passed"] = len(matching_idx)
        counters["bypass"] = len(bypass_idx)
        return PrefilterResult(
            matching=[(new_tenders[i], verdicts[i].matched_kw) for i in matching_idx],
            uzex_bypass=[(new_tenders[i], verdicts[i].matched_kw) for i in bypass_idx],
            verdicts=verdicts,
            counters=counters,
        )

    # Stage: message_type — only tenders and customer requests alert
    alive = []
    for i, t in enumerate(new_tenders):
        if t.message_type in _ALERT_TYPES:
            alive.append(i)
        else:
            verdicts[i].dropped_at = DropStage.MESSAGE_TYPE
    info_count = total_input - len(alive)
    if info_count:
        logger.info("[Alerts] Skipped %d info/ads (not tender or customer_request)", info_count)

    # Stage: supplier-catalog e-shop sources (sell-side, no buyer demand)
    nxt = []
    for i in alive:
        if new_tenders[i].source not in _NO_PUSH_SOURCES:
            nxt.append(i)
        else:
            verdicts[i].dropped_at = DropStage.NO_PUSH_SOURCE
    _nopush = len(alive) - len(nxt)
    if _nopush:
        logger.info("[NoPush] Dropped %d e-shop/catalog alerts (supplier-side, not buyer demand)", _nopush)
    alive = nxt

    # Stage: suppress our own postings (Winch is the vendor on e-shop listings)
    _own = [new_tenders[i] for i in alive if _is_own_lot(new_tenders[i].organization)]
    if _own:
        logger.info("[Self-Lot] Suppressed %d own postings: %s",
                    len(_own), ", ".join((t.title or "")[:40] for t in _own))
    nxt = []
    for i in alive:
        if _is_own_lot(new_tenders[i].organization):
            verdicts[i].dropped_at = DropStage.OWN_LOT
        else:
            nxt.append(i)
    alive = nxt

    # Stage: minimum lot value
    nxt = []
    for i in alive:
        t = new_tenders[i]
        if t.price is None or t.price >= MIN_PRICE:
            nxt.append(i)
        else:
            verdicts[i].dropped_at = DropStage.MIN_PRICE
    # Quirk preserved: counted from the ORIGINAL input, so this log also fires
    # when earlier stages dropped rows and this one dropped none.
    low_price_count = total_input - len(nxt)
    if low_price_count:
        logger.info("[Alerts] Skipped %d tenders below %dM price threshold", low_price_count, MIN_PRICE // 1_000_000)
    alive = nxt

    # Stage: expired deadlines
    nxt = []
    for i in alive:
        if not _is_deadline_expired(new_tenders[i], now=now):
            nxt.append(i)
        else:
            verdicts[i].dropped_at = DropStage.DEADLINE_EXPIRED
    expired_count = len(alive) - len(nxt)
    if expired_count:
        logger.info("[Alerts] Skipped %d tenders with expired deadlines", expired_count)
    alive = nxt

    # Stage: stale (deadline >STALE_DAYS in the past) — see note on STALE_DAYS
    if now is None:
        _ref_now = datetime.utcnow()
    elif now.tzinfo is not None:
        _ref_now = now.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        _ref_now = now
    _stale_cutoff = _ref_now - timedelta(days=STALE_DAYS)
    nxt = []
    stale_count = 0
    for i in alive:
        dt = _parse_deadline(new_tenders[i].deadline)
        if dt is None:
            nxt.append(i)
            continue
        # Strip tzinfo if present so the comparison is always naive vs naive.
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        if dt >= _stale_cutoff:
            nxt.append(i)
        else:
            stale_count += 1
            verdicts[i].dropped_at = DropStage.STALE
    if stale_count:
        logger.info("[Alerts] Skipped %d stale tenders (deadline >1 year past)", stale_count)
    alive = nxt

    # TNVED scope resolution — at THIS position, not earlier: the loader's SELECT
    # historically ran here, and its httpx log line must keep its place in the
    # crawl-log stream (see docstring).
    if tnved_scope is None and tnved_scope_loader is not None:
        tnved_scope = tnved_scope_loader()
    tnved_scope = tnved_scope or []

    # Stage: keyword match, with ТНВЭД-prefix fallback (language-agnostic recall)
    matched_idx = []
    for i in alive:
        t = new_tenders[i]
        kw = _find_matching_keyword(t, keywords)
        if not kw and tnved_scope:
            _tn = str((t.extra_info or {}).get("tnved") or (t.extra_info or {}).get("code") or "")
            if _tn and any(_tn.startswith(p) for p in tnved_scope):
                kw = "тнвэд:%s" % _tn[:4]
        if kw:
            verdicts[i].matched_kw = kw
            matched_idx.append(i)
        else:
            verdicts[i].dropped_at = DropStage.NO_KEYWORD

    if not matched_idx:
        logger.info("[Alerts] No tenders match alert keywords (%d checked)", total_input)
        return _result([], [])

    logger.info("[Alerts] %d tenders match keywords (out of %d new)", len(matched_idx), total_input)

    # Stage: fast reject by title
    before_reject = len(matched_idx)
    kept = []
    for i in matched_idx:
        if any(rej in new_tenders[i].title.lower() for rej in _REJECT_TITLES):
            verdicts[i].dropped_at = DropStage.REJECT_TITLE
        else:
            kept.append(i)
    rejected_fast = before_reject - len(kept)
    if rejected_fast:
        logger.info("[Fast Reject] Removed %d non-relevant tenders by title", rejected_fast)

    # Split: UZEX-family category fast-pass bypasses the AI gate
    bypass_idx = []
    rest_idx = []
    for i in kept:
        t = new_tenders[i]
        title_l = (t.title or "").lower()
        if t.source in _UZEX_PASSTHROUGH_SOURCES and any(h in title_l for h in _UZEX_NICHE_HINTS):
            verdicts[i].uzex_bypass = True
            bypass_idx.append(i)
        else:
            rest_idx.append(i)
    if bypass_idx:
        logger.info("[UZEX Pass] %d UZEX-family tenders bypass AI by category match", len(bypass_idx))

    if not rest_idx and not bypass_idx:
        logger.info("[Alerts] All tenders rejected by fast filter")
        return _result([], [])

    for i in rest_idx + bypass_idx:
        verdicts[i].passed = True
    return _result(rest_idx, bypass_idx)


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
    # Reverse auction badge — distinct bidding dynamic (price goes down).
    if tender.source in _REVERSE_AUCTION_SOURCES:
        parts.append("🔄 *Обратный тендер* (аукцион на понижение)")
    # Live demand signal — real bidders/participants (auctions & RFPs). >0 = someone
    # actually wants this now (deep-think 2026-07-01); absent on passive e-shop lots.
    if tender.bid_count and tender.bid_count > 0:
        parts.append("🔨 *Уже торгуются: %d* — спрос есть" % tender.bid_count)
    parts.append("*%s*" % _escape_md(tender.title[:200]))
    if tender.organization:
        # E-shop sources map organization to producer COUNTRY (УЗБЕКИСТАН/КИТАЙ),
        # not a buyer — «Заказчик: КИТАЙ» misled (2026-07-03). Label honestly.
        _org_label = ("Производство" if "э-магазин" in (tender.source or "").lower()
                      else "Заказчик")
        parts.append("%s: %s" % (_org_label, _escape_md(tender.organization)))
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


# ── Phase-2 delivery routing (deep-think 2026-07-01) ──────────────────────────
# High-signal → per-alert PUSH (interrupt earned); everything else → ONE compact
# ranked DIGEST. Fixes the bimodal-job/unimodal-channel mismatch that drowned the
# few winnable orders in ~200 equal-weight pushes.
_PUSH_PRICE_FLOOR = 100_000_000  # 100M UZS — big-ticket always pushes


def _route_to_push(t: RawTender, mutes: set) -> bool:
    """Push-vs-digest routing decision (module-level so it is unit-testable).
    customer_request overrides a source-mute — a real client asking to buy NOW is
    recall we never trade away; everything else needs high signal AND an unmuted source."""
    if getattr(t, "message_type", None) == "customer_request":
        return True
    return _is_high_signal(t) and t.source not in mutes


def _is_high_signal(t: RawTender) -> bool:
    """True → per-alert push; False → digest."""
    if getattr(t, "message_type", None) == "customer_request":
        return True  # hot lead: a client asking to buy NOW
    if any(m in (t.source or "").lower() for m in _SUPPLIER_CATALOG_MARKERS):
        # Supplier-catalog listings NEVER earn a push (Daniyar's locked decision:
        # e-shop → только по старту аукциона). The big-ticket override leaked
        # them back (#5005 298M / #5006 100M, 2026-07-03) — a supplier's asking
        # price is not demand. Digest-only, no overrides.
        return False
    if t.source in _REVERSE_AUCTION_SOURCES and (t.bid_count or 0) > 0:
        return True  # live reverse auction with real bidders
    if t.price and t.price >= _PUSH_PRICE_FLOOR:
        return True  # big-ticket (>=100M) — worth an interrupt regardless
    if (t.relevance_score or 0) >= 95 and t.price and t.price >= 30_000_000:
        return True  # near-certain "наш заказ" AND non-trivial size
    dt = _parse_deadline(t.deadline)
    if dt is not None:
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        # _parse_deadline floors to the DATE (drops time-of-day), so a lot closing
        # later today parsed to 00:00 was ALREADY in the past → the old
        # `(dt - now) >= 0` check silently sent urgent lots to the digest instead
        # of push (real bug, caught 2026-07-06). Compare by date: closes today or
        # tomorrow = last-chance = push.
        days_left = (dt.date() - datetime.utcnow().date()).days
        if 0 <= days_left <= 1:
            return True  # last-chance (closes today/tomorrow)
    return False


def _digest_score(t: RawTender) -> float:
    """Digest rank: value(log price) × demand(relevance_score)."""
    import math
    value = math.log10((t.price or 0) + 10)  # ~1..11
    demand = (t.relevance_score if t.relevance_score is not None else 60) / 100.0
    return value * (0.4 + demand)


def _build_digest_text(tenders: List[RawTender]) -> str:
    from crawler.core.snap import is_broken_spa
    ranked = sorted(tenders, key=lambda t: -_digest_score(t))
    n = len(tenders)
    parts = ["📋 *Дайджест* — %d менее срочных лотов (не требуют мгновенной реакции)" % n, ""]
    for t in ranked[:10]:
        price = "{:,.0f} сум".format(t.price) if t.price else "цена н/у"
        line = "• *%s* — %s" % (_escape_md((t.title or "")[:48]), price)
        if t.source_url and not is_broken_spa(t.source):
            line += "\n  %s" % t.source_url
        parts.append(line)
    if n > 10:
        parts.append("\n…и ещё %d — все на https://parsing-seo.vercel.app/tenders" % (n - 10))
    return "\n".join(parts)


async def _send_digest(tenders: List[RawTender]) -> bool:
    """Send ONE compact ranked digest message. Returns True on HTTP 200."""
    if not tenders:
        return False
    url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, json={
            "chat_id": settings.telegram_alert_chat_id,
            "text": _build_digest_text(tenders),
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
            "protect_content": True,
        })
    ok = resp.status_code == 200
    logger.info("[Digest] %s — %d items in one message",
                "sent" if ok else "FAILED %d" % resp.status_code, len(tenders))
    return ok


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

    # Deterministic stages 2-11 — extracted to prefilter() 2026-07-27 so replay
    # and the version benchmark run the SAME code, not a drifting copy. The
    # TNVED scope consult (shadow-promoted recall layer) goes in as a lazy
    # loader so its SELECT keeps its historical position in the log stream; it
    # is empty until a shadow candidate is promoted, so a safe no-op by default.
    pf = prefilter(new_tenders, keywords, tnved_scope_loader=_load_tnved_scope)
    matching = pf.matching
    uzex_bypass = pf.uzex_bypass

    if not matching and not uzex_bypass:
        # prefilter already logged which gate emptied the batch
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
                # Customer requests (TG group leads): the heavy product-scope
                # prompt misjudges colloquial orders ("сумка 1250шт" → 0), so we
                # don't run it. But fully exempting them flooded alerts with ~30%
                # noise (12.06 audit: ad self-promo Eco Print ×10, vacancies
                # "usta kerak", non-print tech). Run the LIGHT spam gate instead:
                # drops pure spam / out-of-scope (sewing, cutting/signage per
                # Daniyar 12.06), keeps every real order. Fail-open.
                if getattr(tender, "message_type", None) == "customer_request":
                    if await _ai_lead_is_spam(tender, ai_client):
                        return tender, kw, RelevanceResult(
                            is_relevant=False, score=0,
                            category="ad", reason="lead spam/out-of-scope")
                    return tender, kw, _allow("customer_request lead: kept")
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

    # ── 3-tier routing: high-signal → per-alert PUSH; the rest → one ranked DIGEST.
    # Feedback auto-mute (Tier-1): a source marked ❌≥N with 0 ✅ routes to digest.
    from crawler.core.feedback import get_active_mutes
    _mutes = get_active_mutes()

    def _to_push(t):
        return _route_to_push(t, _mutes)

    digest_tenders = [t for t, _kw in matching if not _to_push(t)]
    matching = [(t, kw) for t, kw in matching if _to_push(t)]
    # Always log routing (even 0 digest): a crawl that pushes everything with "0 muted
    # sources" is the silent mute-read failure that leaked muted sources to push — now visible.
    logger.info("[Route] %d push / %d digest (%d muted sources)",
                len(matching), len(digest_tenders), len(_mutes))
    if not _mutes:
        logger.warning("[Route] mute set EMPTY — every muted source pushes this crawl (DB read likely failed)")

    # Hot leads first: customer_request items (real clients asking to buy NOW)
    # jump the queue — lowest seq + sent before tender noise. Stable sort keeps
    # relative order within each group.
    matching.sort(key=lambda tk: 0 if getattr(tk[0], "message_type", None) == "customer_request" else 1)

    # ── Pre-send live verification (V1, 2026-07-02): re-check each PUSH lot on
    # its source platform; drop confirmed closed/gone («алерт есть — тендера нет»).
    # Fail-open inside — uncertainty always sends. Digest items skip this (cost).
    if matching and not dry_run:
        try:
            from crawler.core.verifier import verify_push_batch
            matching = await verify_push_batch(matching)
        except Exception as _vexc:
            logger.warning("[Verify] batch failed open (all sent): %s", str(_vexc)[:100])

    # Reserve sequential alert numbers
    from crawler.core.feedback import get_next_seq, save_alert_seq
    start_seq = get_next_seq(len(matching)) if matching else 0

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
            # Inline keyboard for feedback \u2014 context-aware wording. "\u041a\u043b\u0438\u0435\u043d\u0442" made no
            # sense on a tender \u2192 feedback dead 2 months (last click 2026-04-15).
            # Leads keep \u041a\u043b\u0438\u0435\u043d\u0442/\u0420\u0435\u043a\u043b\u0430\u043c\u0430/\u041c\u0438\u043c\u043e; tenders get \u0418\u043d\u0442\u0435\u0440\u0435\u0441\u043d\u043e/\u0420\u0435\u043a\u043b\u0430\u043c\u0430/\u041d\u0435 \u043c\u043e\u0451.
            # Same callback semantics (ok=keep, ad=spam, skip=FP) \u2192 feedback_bot and
            # in-flight old alerts unchanged; only the visible label adapts.
            if tender.message_type == "customer_request":
                _fb_row = [
                    {"text": "\U0001f464 \u041a\u043b\u0438\u0435\u043d\u0442", "callback_data": "fb:%d:ok" % seq},
                    {"text": "\U0001f4e2 \u0420\u0435\u043a\u043b\u0430\u043c\u0430", "callback_data": "fb:%d:ad" % seq},
                    {"text": "\u274c \u041c\u0438\u043c\u043e", "callback_data": "fb:%d:skip" % seq},
                ]
            else:
                _fb_row = [
                    {"text": "\u2705 \u0418\u043d\u0442\u0435\u0440\u0435\u0441\u043d\u043e", "callback_data": "fb:%d:ok" % seq},
                    {"text": "\U0001f4e2 \u0420\u0435\u043a\u043b\u0430\u043c\u0430", "callback_data": "fb:%d:ad" % seq},
                    {"text": "\u274c \u041d\u0435 \u043c\u043e\u0451", "callback_data": "fb:%d:skip" % seq},
                ]
            reply_markup = {"inline_keyboard": [_fb_row]}

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

    if matching:
        logger.info("[Alerts] Sent %d / %d alerts (seq #%d-#%d)", sent, len(matching), start_seq, start_seq + len(matching) - 1)

    # Ranked digest for the low-signal tail — separate client, bulkheaded: a digest
    # failure must NEVER affect the push path above (Newman). Mark digested items
    # alerted so a same-content re-issue is later suppressed by the dedup window.
    if digest_tenders:
        try:
            if await _send_digest(digest_tenders):
                _dstart = get_next_seq(len(digest_tenders))
                for _j, _t in enumerate(digest_tenders):
                    save_alert_seq(_t.external_id, _t.source, _dstart + _j)
        except Exception as _exc:
            logger.warning("[Digest] send failed (push unaffected): %s", str(_exc)[:120])

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

    # Сводку БЕЗ ошибок слать только раз в неделю (понедельник UTC) — по просьбе
    # Данияра (30.06): ежедневный crawl-дайджест шумит. Ошибки (реальные поломки
    # сбора) шлём всегда, сразу — они требуют действия.
    from datetime import datetime as _dt, timezone as _tz
    if not errors and _dt.now(_tz.utc).weekday() != 0:
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
