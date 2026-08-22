"""Пины дотягивания предмета лота у предквалификаций (22.08).

Из чего выросло. Данияр прокликал алерты дороже 15 млн — два самых дорогих
оказались мимо по одной причине, и виноват был не AI:

  #7751, 349 млн — заголовок «Услуги издательские», предмет «Услуга публикации
                   статьи»;
  #7737,  24 млн — заголовок «Услуги печатные и услуги по копированию…»,
                   предмет «Услуга по установке баннера» ×2 (баннеры прямо
                   записаны как OUT в product-scope промпта).

В `sources.yaml` у источника `title: categoryName`, а список `GetLots` отдаёт
ТОЛЬКО категорию. В базе лежало `search_text = "Услуги издательские
\\"O`ZBEKTELEKOM\\" AJ"` — предмета нет вовсе, модель судила по названию рубрики.

Свойства, которые тут держатся:
  • трогаем ТОЛЬКО предквалификации — чужие источники не задеваются;
  • повторы позиций схлопываются (у 100279 баннер стоял дважды);
  • дописывание идемпотентно — ночные перепрогоны не раздувают строку и не
    выталкивают полезное за срез в 320 знаков, с которым промпт её читает;
  • отказ детали НЕ обнуляет search_text: пустая строка хуже категории, модель
    увидела бы вообще ничего;
  • обогащение стоит ДО send_alerts. Дыра двусторонняя: без предмета не только
    чужой лот проходит гейт, но и НАШ не проходит, если категория непрофильная
    («Услуги общественных организаций» + «печать буклетов» → отсев ДО AI).

Run: python3 -m crawler.tests.test_prequal_detail   (exit 1 on any failure)
"""
import asyncio
import io
import os
import sys
import types

if "crawler.config.settings" not in sys.modules:
    _m = types.ModuleType("crawler.config.settings")
    _m.settings = types.SimpleNamespace(
        telegram_bot_token="", telegram_alert_chat_id="", openrouter_api_key="",
        alert_keywords="", ai_score_threshold=70,
        ai_relevance_model="x", ai_relevance_model_fast="x",
        supabase_url="", supabase_service_role_key="",
    )
    sys.modules["crawler.config.settings"] = _m

from crawler.core.prequal_detail import (
    PREQUAL_SOURCE, enrich, lot_id, merged_search_text, positions_from_detail,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))


# --- id лота ----------------------------------------------------------------

def test_lot_id_plain_and_prefixed():
    assert lot_id("100486") == "100486"
    assert lot_id("uzex-prq-100486") == "100486"


def test_lot_id_on_garbage():
    for bad in (None, "", "no-digits-here"):
        assert lot_id(bad) is None, bad


# --- позиции ----------------------------------------------------------------

def test_positions_read_product_names():
    """Живая выдача GetLot?id=100486."""
    data = {"categoryName": "Услуги издательские",
            "details": [{"productName": "Услуга публикации статьи"}]}
    assert positions_from_detail(data) == ["Услуга публикации статьи"]


def test_repeated_position_is_collapsed():
    """У лота 100279 «Услуга по установке баннера» стоит ДВАЖДЫ — для модели
    это одно слово дважды, только шум и лишние токены."""
    data = {"details": [{"productName": "Услуга по установке баннера"},
                        {"productName": "Услуга по установке баннера"}]}
    assert positions_from_detail(data) == ["Услуга по установке баннера"]


def test_positions_order_is_preserved():
    data = {"details": [{"productName": "Б"}, {"productName": "А"}]}
    assert positions_from_detail(data) == ["Б", "А"]


def test_positions_on_garbage_input():
    for bad in (None, "строка", {}, {"details": None}, {"details": ["x", 5]}):
        assert positions_from_detail(bad) == [], bad


# --- склейка ----------------------------------------------------------------

_BASE = 'Услуги издательские "O`ZBEKTELEKOM " AJ'


def test_subject_is_appended_to_the_category():
    out = merged_search_text(_BASE, ["Услуга публикации статьи"])
    assert _BASE in out and "Услуга публикации статьи" in out


def test_append_is_idempotent():
    """Иначе ночные перепрогоны раздували бы строку и выталкивали полезное за
    срез в 320 знаков, с которым её читает промпт."""
    once = merged_search_text(_BASE, ["Услуга публикации статьи"])
    twice = merged_search_text(once, ["Услуга публикации статьи"])
    assert twice is None, twice


def test_nothing_to_add_returns_none_not_empty():
    assert merged_search_text(_BASE, []) is None


def test_missing_detail_never_blanks_the_text():
    """ГЛАВНОЕ свойство отказа. Пустой search_text хуже категории: модель
    увидела бы вообще ничего и решала бы по одному заголовку."""
    assert merged_search_text(_BASE, []) is None
    # вызывающий при None оставляет прежнее значение — пин на источник:
    src = io.open(os.path.join(_ROOT, "crawler/core/prequal_detail.py"), encoding="utf-8").read()
    i = src.index("merged = merged_search_text")
    assert "if not merged:" in src[i:i + 200]
    assert "continue" in src[i:i + 200]


def test_long_position_list_is_capped():
    out = merged_search_text("кат", ["позиция номер %d" % i for i in range(200)])
    assert len(out) < 400, len(out)


def test_works_when_there_was_no_search_text():
    out = merged_search_text(None, ["Печать буклетов"])
    assert out == "Печать буклетов"


# --- границы ----------------------------------------------------------------

class _T(object):
    def __init__(self, source, ext="1", st="x"):
        self.source, self.external_id, self.search_text = source, ext, st
        self.extra_info = {}


def test_other_sources_are_never_touched():
    """Фильтр по точному имени стоит первым: ни одного сетевого вызова и ни
    одной мутации для чужих источников."""
    others = [_T("ETender UZEX"), _T("Cooperation.uz Лоты"), _T("Anor Bank")]
    n = asyncio.run(enrich(others, dry_run=True))
    assert n == 0
    assert all(t.search_text == "x" and t.extra_info == {} for t in others)


def test_empty_input_is_a_noop():
    assert asyncio.run(enrich([], dry_run=True)) == 0
    assert asyncio.run(enrich(None, dry_run=True)) == 0


def test_enrichment_runs_before_alerts_not_after():
    """Место важнее самой правки: после префильтра лот с непрофильной
    категорией уже отсеян ключевым гейтом и до AI не доедет."""
    src = io.open(os.path.join(_ROOT, "crawler/core/runner.py"), encoding="utf-8").read()
    assert src.index("_enrich_prequal") < src.index("from crawler.core.notifier import send_alerts")


def test_enrichment_failure_does_not_stop_alerts():
    """Обогащение — улучшение, а не условие работы."""
    src = io.open(os.path.join(_ROOT, "crawler/core/runner.py"), encoding="utf-8").read()
    i = src.index("_enrich_prequal")
    block = src[i - 200:i + 400]
    assert "try:" in block and "except Exception" in block


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as exc:
            print("FAIL", fn.__name__, "%s: %s" % (type(exc).__name__, exc))
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
