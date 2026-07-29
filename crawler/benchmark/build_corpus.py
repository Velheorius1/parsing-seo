"""Rebuild crawler/benchmark/corpus_v1.json from its sources.

The corpus is a checked-in data file, but it must not be a magic one: the first
hand-assembled version scored 42% of its entries through the WRONG gate (TG
lead texts were marked as ordinary tenders, so the relevance classifier judged
them instead of the lead spam gate they actually hit in production). This script
makes the assembly auditable and repeatable.

Sources:
  1. logs/corpus_candidates_banks.json — customer_audit --export-corpus
  2. alert_feedback                    — human-clicked labels (the strongest truth we own)
  3. real noise rows from `tenders`    — regex bait that is not our order
  4. GOLDEN from score_relevance       — the 06.06 product-scope set

Labels vs expectations (the distinction the whole score rests on):
  label            — is this a real print order in the WORLD
  expect_delivered — what policy says the pipeline should do TODAY
A cheap-but-real lot is label=relevant + expect_delivered=False, so a price
threshold change shows up as a deliberate rebaseline, never as a silent drift.

Run on the VPS (needs prod deps + DB):
  python3 -m crawler.benchmark.build_corpus > /tmp/corpus_v1.json
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, "/opt/parsing-seo")

# Sources whose rows are customer requests: in production they carry
# message_type="customer_request" and are judged by _ai_lead_is_spam, not by
# _ai_check_relevance. Scoring them with the tender classifier measures a gate
# that never runs on them.
_LEAD_SOURCES = ("TG: PR Media Group (запросы клиентов)",)

# Corrections to the human labels, confirmed by Daniyar 2026-07-28.
#
# The corpus derives labels mechanically from alert_feedback.corrected_label,
# so a mis-click becomes benchmark ground truth and the gate gets punished for
# being RIGHT. These three were flagged during the lead-precision work because
# they contradict explicit keep-rules of the prompt (stickers and boxes are our
# profile) — Daniyar confirmed the clicks, not the rules, were wrong.
#
# Keyed by an exact text prefix so a re-run of this script cannot silently
# revert them. NOTE: the clicks themselves still sit in alert_feedback with the
# old labels and still feed Monday's playbook_refine — fixing prod data is a
# separate, deliberate call.
_LABEL_OVERRIDES = {
    "Нужен исполнитель для печати наклеек на листе":
        ("client", "печать наклеек — прямо в keep-списке промпта"),
    "Изготовление именной подарочной коробки срочно":
        ("client", "коробка — наш профиль; единичность не делает заказ чужим"),
    "sergelida UV pechat kimda bor, bitta qutuni ustiga":
        ("client", "запрос УФ-печати на коробке, помечен как реклама по ошибке"),
}

_NOISE_TERMS = ["Изолента", "Стакан бумажный", "Портландцемент",
                "Указатель высокого", "Марля полиграфическая", "NFC визитк"]

GOLDEN_REL = [
    ("Изготовление выставочного информационного стенда из ЛДСП", "client"),
    ("Табличка информационная настольная, печать", "client"),
    ("Бейджик горизонтальный 100 штук", "client"),
    ("Бланки строгого учёта, печать 5000 шт", "client"),
    ("Поставка коробок из гофрокартона для упаковки", "client"),
    ("Услуги издательские", "client"),
    ("Каталоги и брошюры полноцветная печать", "client"),
    ("Баннер на фасад здания 3х6 м, наружная реклама", "ad"),
    ("Папка-скоросшиватель А4 канцелярская офисная", "irrelevant"),
    ("Указатель высокого напряжения УВН-10", "irrelevant"),
    ("Портландцемент ЦЕМ II А-И 32,5 Н в мешках", "irrelevant"),
    ("Поставка компьютеров и оргтехники", "irrelevant"),
]

# Смешанная корзина (добавлено 29.07, корпус v2 → v2.1). Класс, на котором гейт
# терял лиды: в заявке есть И наша позиция, И чужая — модель цеплялась за первый
# чужой пункт. Отрицательные записи здесь не менее важны положительных: они
# держат границу, за которой правило превратилось бы в «в перечне попалось наше
# слово → пропустить» (коробка распределительная — электрика, краски
# полиграфические — расходники коллеги-типографии).
# Тексты взяты из живого потока один-в-один: пересказ мерил бы пересказ.
GOLDEN_LEADS = [
    ("нужен пошив футболки с нанесением логотипа, цвета и дизайн должны совпадать "
     "с образцом huddi shunaqa futbolka qilib berish kere kim qiberolidi, ranglari "
     "bilan bir xil qilib, logotipi bilan футболка, логотип, нанесение, пошив",
     "client", "пошив чужой, НАНЕСЕНИЕ ЛОГОТИПА наше"),
    ("изготовление кожаных стаканов с логотипом, брендированных капхолдеров и "
     "брендированных стеклянных стаканов здравствуйте! кто может сделать кожаные "
     "стаканы с логотипом для ручек! и еще нужны надстаканники (капхолдера) "
     "брендированные для стаканов и брендированные стаканы (обычные, прочное "
     "стекло) кожаные стаканы, капхолдеры, брендированные стаканы, стеклянные "
     "стаканы, логотип",
     "client", "изготовление кожи чужое, брендирование наше"),
    ("добрый день. ищем компанию которая может сделать полный комплект с ляганом "
     "(корпоративный подарок). добрый день. ищем компанию которая может сделать "
     "полный комплект с ляганом (корпоративный подарок). комплектация: -ляган "
     "ручной работы - фирменный пакет - дизайнерская коробка/ футляр - деревянные "
     "подставки. нужно срочно, до субботы. отправляйте цены и пример.",
     "client", "ляган и подставки чужие, фирменный пакет и коробка наши"),
    ("нужен мастер для вырубки на готовой коробке салом таййор коробкага вырубка "
     "килиш керак ким килиб беролади? вырубка, коробка",
     "client", "вырубка по готовой коробке — послепечатная операция"),
    ("Водяной насос Водонагреватель электрический Коробка распределительная Короб "
     "кабельный LED панель Кабель силовой с алюминиевой жилой на напряжение до 1 "
     "кВ Выключатель автоматический на напряжение более 1 кВ Щит распределительный "
     "Подрозетник пластмассовый Выключатель неавтоматический Розетка штепсельная "
     "бытового назначения",
     "irrelevant", "«коробка» здесь РАСПРЕДЕЛИТЕЛЬНАЯ — омоним, не упаковка"),
    ("Концентрат увлажняющего раствора для листовой печати Краски полиграфические "
     "специального назначения прочие Смывка Растворители и разбавители органические "
     "сложные; составы готовые для удаления красок и лаков (смывки) Проявитель для "
     "термальных офсетных СТР пластин Концентрат увлажняющего раствора для ролевой "
     "печати Краски полиграфические для офсетной печати",
     "irrelevant", "расходники печати — так закупается типография-коллега"),
]

# Мерч из не-бумажных материалов с нанесением (добавлено 29.07, v2.1 → v2.2).
# Решение Данияра «берём»: предмет делает подрядчик, наше — нанесение. Ось не
# материал, а наличие нанесения, поэтому отрицательные записи здесь держат обе
# стороны: закупка одежды без логотипа и литьё силикона (последнее уже лежит в
# корпусе как c0066/c0042 — метки Данияра, их правка не сдвинула).
GOLDEN_LEADS_MERCH = [
    (
     "изготовление 50 штук надстаканников из дерева или кожи с логотипом "
     "здравствуйте! кто может такие надстаканники из дерева или кожи сделать "
     "50 шт с лого? надстаканники, дерево, кожа, логотип",
     "client", "надстаканники дерево/кожа С ЛОГОТИПОМ — нанесение наше"),
    (
     "нужен изготовитель деревянных бейджиков день добрый. кто занимается "
     "изготовлением таких деревянных бейджиков? деревянные бейджики",
     "client", "деревянный бейджик — предмет чужой, брендирование наше"),
    (
     "срочно нужен кубок с гравировкой всем ассаламу алейкум срочно нужен "
     "кубок 🏆 с гравировкой кубок, гравировка",
     "client", "кубок с гравировкой — награда с нанесением"),
    (
     "добрый день, кто может за день изготовить именную табличку на стол "
     "руководителя с гравировкой? добрый день, кто может за день изготовить "
     "именную табличку на стол руководителя с гравировкой?",
     "client", "именная табличка с гравировкой"),
    (
     "добрый вечер, нужен поставщик для изготовления добрый вечер, нужен "
     "поставщик для изготовления 1) кожаных, брендированных салфетниц 2) "
     "информационных указателей из дерева контакты для связи: 998200082 "
     "______________ xayrli kech, quyidagilarni tayyorlash uchun yetkazib "
     "beruvchi kerak: 1) charmdan yasalgan brendli salfetka idishlar 2) "
     "yog‘ochdan yasalgan axborot belgilari bog‘lanish uchun: 998200082",
     "client", "кожаные БРЕНДИРОВАННЫЕ салфетницы + указатели из дерева"),
    (
     "ассалому алейкум футболка кепка керак ассалому алейкум футболка кепка "
     "керак тел : 93-955-70-00",
     "irrelevant", "КОНТРОЛЬ: «футболка кепка керак» без нанесения — закупка одежды"),
]

# Найдено независимой проверкой Данияра 29.07 (два живых лота), корпус v2.2 → v2.3.
# Оба — реальные закупки банков, и оба показали дыры РАЗНЫХ стадий, поэтому
# лежат здесь как якоря: лид-запись ловит регрессию гейта, pipeline-запись —
# регрессию ключевиков и AI-релевантности.
GOLDEN_LEADS_LIVE = [
    ("Картон конверт Конвертлар, махфий хатлар",
     "client",
     "встречный аукцион Sanoat-Qurilish Bank на 234 850 000 сум; бумага и картон "
     "наш товар и БЕЗ нанесения — на этой записи я сам сломал гейт 29.07"),
]

# pipeline-запись: судится префильтром (ключевики!) и AI-релевантностью.
GOLDEN_PIPELINE = [
    {
        "external_id": "26120012502812",
        "source": "ETender UZEX",
        "title": "Mastercard kartalari uchun kartholder ishlab chiqarish xizmatlarini xarid qilish",
        "organization": "XALQ BANK",
        "search_text": ("Mastercard kartalari uchun kartholder ishlab chiqarish "
                        "xizmatlarini xarid qilish XALQ BANK"),
        "price": 1225574400.0,
        "currency": "UZS",
        "deadline": "2026-07-24T08:56:45",
        "frozen_now": "2026-07-24T04:00:10.520871+00:00",
        "label": "relevant",
        "expect_delivered": True,
        "category": "client",
        "note": ("Xalq Bank, 1 225 574 400 сум. Лежал в БД с 24.07 неотправленным: "
                 "ни один из 134 ключевиков не совпал, AI его не видел. Якорь на "
                 "стадию ключевиков — правка промпта такое не ловит"),
    },
]

MIN_PRICE_POLICY = 5_000_000


class Builder(object):
    def __init__(self):
        self.entries = []

    def add(self, **kw):
        e = {"cid": "c%04d" % (len(self.entries) + 1)}
        e.update(kw)
        e.setdefault("added", "2026-07-28")
        e.setdefault("since", "v1")
        e.setdefault("retired", None)
        e.setdefault("expect_route", None)
        self.entries.append(e)

    def snapshot(self, r, **kw):
        extra = dict((str(k), v if isinstance(v, str) else str(v))
                     for k, v in (r.get("extra_info") or {}).items() if v is not None)
        base = dict(
            kind="pipeline", external_id=r.get("external_id"), source=r.get("source"),
            title=r.get("title") or "", organization=r.get("organization") or "",
            search_text=r.get("search_text") or "", price=r.get("price"),
            currency=r.get("currency") or "UZS", deadline=r.get("deadline"),
            extra_info=extra, message_type=r.get("message_type") or "tender",
            bid_count=r.get("bid_count"), status=r.get("status") or "active",
            # Judge every dated row as of its own collection day, forever —
            # otherwise the corpus rots and "recall fell" is the calendar.
            frozen_now=str(r.get("collected_at") or "2026-07-28T00:00:00+00:00"),
        )
        base.update(kw)
        self.add(**base)


def _client():
    from crawler.core.db import _get_client
    return _get_client()


def add_bank_finds(b, path):
    if not os.path.exists(path):
        print("  (no bank export at %s — skipped)" % path, file=sys.stderr)
        return
    for r in json.load(open(path, encoding="utf-8")):
        title = (r.get("title") or "").lower()
        price = r.get("price") or 0
        is_print = any(w in title for w in
                       ("издательск", "стикер", "флаер", "закладка", "bxmlar", "печат"))
        label = "relevant" if is_print else "irrelevant"
        deliver = is_print and (not price or price >= MIN_PRICE_POLICY)
        # A deadline already past at collection time is a record, not an
        # opportunity — policy says drop regardless of topic.
        if (r.get("replay") or {}).get("dropped_at") == "deadline_expired":
            deliver = False
        org = (r.get("organization") or "").lower()
        bank = "SQB" if ("пром" in org or "sanoat" in org) else "Xalq"
        b.snapshot(r, label=label, expect_delivered=bool(deliver),
                   provenance="bank_audit:%s" % bank,
                   note="capture stage: %s" % ((r.get("replay") or {}).get("dropped_at") or "passed"))


def add_feedback(b, limit_per_label=None):
    from crawler.core.db import query_with_retry
    c = _client()

    def _q():
        return (c.table("alert_feedback")
                .select("corrected_label,message_text,source,created_at")
                .not_.is_("message_text", "null")
                .in_("corrected_label", ["client", "ad", "irrelevant"])
                .order("created_at", desc=True).limit(500).execute())

    rows = query_with_retry(_q, label="fb").data or []
    want = dict(limit_per_label or {"client": 14, "ad": 6, "irrelevant": 14})
    seen = set()
    for r in rows:
        lab = r["corrected_label"]
        txt = (r.get("message_text") or "").strip()
        if len(txt) < 25 or want.get(lab, 0) <= 0 or txt[:90] in seen:
            continue
        seen.add(txt[:90])
        want[lab] -= 1
        src = r.get("source") or ""
        is_lead = src in _LEAD_SOURCES

        note = "clicked by Daniyar; %s gate in prod" % ("lead-spam" if is_lead else "relevance")
        for prefix, (corrected, why) in _LABEL_OVERRIDES.items():
            if txt.startswith(prefix):
                note = "МЕТКА ИСПРАВЛЕНА (%s → %s, 28.07): %s" % (lab, corrected, why)
                lab = corrected
                break

        b.add(kind="lead" if is_lead else "ai_only",
              external_id="fb-%04d" % (len(b.entries) + 1),
              title=txt[:200], search_text=txt[:1200], organization="",
              source=src or "Hayotbirja отбор",
              message_type="customer_request" if is_lead else "tender",
              price=None, deadline=None, extra_info={},
              label="relevant" if lab == "client" else "irrelevant",
              expect_delivered=(lab == "client"), category=lab,
              provenance="feedback:corrected",
              frozen_now="2026-07-28T00:00:00+00:00",
              note=note)


def add_noise(b):
    from crawler.core.db import query_with_retry
    c = _client()
    for term in _NOISE_TERMS:
        def _q(t=term):
            # ORDER BY is not cosmetic here: without it Postgres returns an
            # arbitrary pair each run, so rebuilding the "frozen" corpus quietly
            # swapped 7 noise rows for 7 others (caught by diffing v1.1 vs v2).
            # A benchmark whose contents drift on rebuild is not a benchmark.
            return (c.table("tenders")
                    .select("external_id,source,title,organization,search_text,price,"
                            "currency,deadline,message_type,extra_info,bid_count,status,"
                            "collected_at")
                    .ilike("title", "%%%s%%" % t)
                    .order("external_id").limit(2).execute())
        try:
            for r in (query_with_retry(_q, label="noise").data or []):
                b.snapshot(r, label="irrelevant", expect_delivered=False,
                           provenance="noise:known_fp",
                           note="matches a keyword but is not our order")
        except Exception as exc:
            print("  noise '%s' failed: %s" % (term, str(exc)[:60]), file=sys.stderr)


def add_golden(b):
    for text, lab in GOLDEN_REL:
        b.add(kind="ai_only", external_id="golden-%02d" % (len(b.entries) + 1),
              title=text, search_text=text, organization="",
              source="Hayotbirja отбор", message_type="tender",
              price=None, deadline=None, extra_info={},
              label="relevant" if lab == "client" else "irrelevant",
              expect_delivered=(lab == "client"), category=lab,
              provenance="golden:score_relevance",
              frozen_now="2026-06-06T00:00:00+00:00",
              note="06.06 product-scope golden")


def add_golden_leads(b, skip_texts=()):
    """Смешанные корзины. Зовётся ПОСЛЕДНИМ и только дописывает: cid выдаются по
    порядку вставки, и вклинивание в середину перенумеровало бы весь хвост."""
    for i, (text, lab, why) in enumerate(
            GOLDEN_LEADS + GOLDEN_LEADS_MERCH + GOLDEN_LEADS_LIVE):
        if text in skip_texts:
            continue
        b.add(kind="lead", external_id="mixed-%02d" % (i + 1),
              title=text[:80], search_text=text, organization="",
              source=_LEAD_SOURCES[0], message_type="customer_request",
              price=None, deadline=None, extra_info={},
              label="relevant" if lab == "client" else "irrelevant",
              expect_delivered=(lab == "client"), category=lab,
              provenance="golden:mixed_basket",
              frozen_now="2026-07-29T00:00:00+00:00",
              added="2026-07-29", since="v2.1", note=why)


CORPUS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus_v1.json")


def append_curated(path):
    """Безопасный путь: существующие записи переносятся ДОСЛОВНО, дописывается
    только курированный материал из кода (GOLDEN_LEADS).

    Почему это дефолт, а не --rebuild. Полная пересборка тянет `add_feedback`,
    а тот берёт ПОСЛЕДНИЕ 500 кликов и добивает квоты по меткам — источник
    движется. 29.07 проверено на практике: три метки, исправленные в проде,
    перетасовали заполнение квот, и пересборка переписала 33 существующие
    записи (включая метки), сохранив те же cid. Такой коммит выглядел бы как
    «обновил корпус», а на деле молча сделал бы rebaseline: баллы до и после
    стали бы несравнимы, а причина — невидимой. Поэтому cid считается вечным,
    а корпус-файл — замороженным артефактом, который только дополняют.
    """
    with open(path, encoding="utf-8") as f:
        corpus = json.load(f)
    b = Builder()
    b.entries = list(corpus["entries"])          # cid продолжится с максимума
    before = len(b.entries)
    add_golden_leads(b, skip_texts=set(e.get("search_text") or "" for e in b.entries))
    add_golden_pipeline(b, skip_ids=set(e.get("external_id") or "" for e in b.entries))
    corpus["entries"] = b.entries
    added = len(b.entries) - before
    marker = ("v2 → v2.1 → v2.2 → v2.3 (29.07): ДОБАВЛЕНЫ записи классов «смешанная "
              "корзина» (наша позиция рядом с чужой) и «мерч из дерева/кожи/"
              "стекла с нанесением» — по решению Данияра «берём»; в обоих "
              "случаях вместе с контрольными отрицательными (омонимы, закупка "
              "одежды без логотипа). Метки существующих не тронуты — минорные "
              "версии, дельта внутри v2.x осмысленна. v2.3 — два живых лота из "
              "независимой проверки: «Картон конверт» (лид) и картхолдеры Xalq "
              "Bank (pipeline, якорь на стадию ключевиков).")
    if added and marker not in corpus.get("notes", ""):
        corpus["notes"] = (corpus.get("notes", "") + " " + marker).strip()
    print("дописано %d записей (было %d, стало %d)"
          % (added, before, len(b.entries)), file=sys.stderr)
    return corpus


def rebuild():
    b = Builder()
    add_bank_finds(b, "/opt/parsing-seo/logs/corpus_candidates_banks.json")
    add_noise(b)
    add_golden(b)
    add_feedback(b)
    add_golden_leads(b)
    return b


def add_golden_pipeline(b, skip_ids=()):
    """Курированные ТЕНДЕРЫ (не лиды): проходят префильтр целиком, включая
    ключевики, и судятся AI-релевантностью — то есть ловят регрессии стадий,
    которых лид-записи не касаются."""
    for e in GOLDEN_PIPELINE:
        if e["external_id"] in skip_ids:
            continue
        b.add(kind="pipeline", external_id=e["external_id"], source=e["source"],
              title=e["title"], organization=e["organization"],
              search_text=e["search_text"], price=e["price"],
              currency=e["currency"], deadline=e["deadline"], extra_info={},
              message_type="tender", bid_count=None, status="active",
              frozen_now=e["frozen_now"], label=e["label"],
              expect_delivered=e["expect_delivered"], category=e["category"],
              provenance="live-check:2026-07-29", added="2026-07-29",
              since="v2.3", note=e["note"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true",
                    help="полная пересборка из БД — РЕ-СНИМОК, метки существующих "
                         "записей могут поехать; обязательна сверка diff'ом и "
                         "мажорный bump версии")
    ap.add_argument("--version", default=None, help="corpus_version на выходе")
    ap.add_argument("--corpus", default=CORPUS_PATH)
    a = ap.parse_args()

    if not a.rebuild:
        corpus = append_curated(a.corpus)
        if a.version:
            corpus["corpus_version"] = a.version
        print(json.dumps(corpus, ensure_ascii=False, indent=1))
        return 0

    print("⚠️  --rebuild: это РЕ-СНИМОК alert_feedback, а не воспроизведение. "
          "Сверь diff по cid перед коммитом.", file=sys.stderr)
    b = rebuild()

    corpus = {
        "corpus_version": a.version or "v2.1",
        "created": "2026-07-28",
        "notes": ("Замороженный бенчмарк ВЕРСИИ краулера. Не дублирует weekly_metrics: "
                  "тот меряет живую неделю (данные плавают, версии несравнимы), этот — "
                  "детерминированный прогон одного набора через любой коммит. "
                  "label = правда о мире; expect_delivered = что политика предписывает "
                  "сегодня. kind=lead судится spam-гейтом, как в проде. "
                  "Дописать курированный материал (безопасно, без БД): "
                  "python3 -m crawler.benchmark.build_corpus > new.json. "
                  "Полный ре-снимок из БД: --rebuild (метки могут поехать, "
                  "сверять diff'ом по cid). "
                  "v1.1 → v2 (28.07): исправлены три метки, где клик противоречил "
                  "явным keep-правилам (наклейки, коробки) — подтверждено Данияром. "
                  "Смена меток = мажорная версия, баллы v1.1 и v2 несравнимы. "
                  "v2 → v2.1 (29.07): ДОБАВЛЕНЫ 6 записей класса «смешанная корзина» "
                  "(4 keep + 2 контрольных омонима), метки существующих не тронуты — "
                  "минорная версия, дельта внутри v2.x осмысленна."),
        "entries": b.entries,
    }
    print(json.dumps(corpus, ensure_ascii=False, indent=1))
    st = collections.Counter(e["kind"] for e in b.entries)
    print("entries=%d kinds=%s deliver=%s" % (
        len(b.entries), dict(st),
        dict(collections.Counter(e["expect_delivered"] for e in b.entries))),
        file=sys.stderr)


if __name__ == "__main__":
    main()
