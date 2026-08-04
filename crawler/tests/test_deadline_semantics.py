"""Пин: дата публикации не попадает в срок подачи (04.08).

Класс дефекта. Корпоративные и банковские «тендеры» лежат новостной лентой, и
единственная дата на карточке — когда объявление вывесили. Пока она хранилась
в `deadline`, гейт `_is_deadline_expired` (grace 1 день) убивал КАЖДЫЙ лот
источника через сутки после появления. Источник при этом выглядел здоровым:
строки собираются, ошибок нет, алертов ноль — то есть провал неотличим от
«площадка ничего не публикует».

Так молчал Anor Bank: 37 строк за всё время, алертов 2, при том что лоты прямо
профильные — «изготовление самоклеящихся наклеек формата A5», «конверты для
банковских карт», «производство корпоративного мерча». Проверено по четырём
живым карточкам: на страницах лотов срока приёма заявок нет вообще,
единственное упоминание срока — «Срок изготовления: не более 20 календарных
дней с момента подписания договора», то есть про производство, а не про заявки.

Тесты держат три вещи: флаг уводит дату в `date_start` и оставляет `deadline`
пустым; БЕЗ флага поведение прежнее (иначе правка тихо поменяла бы 18 других
HTML-источников); и конфиги источников, где это уже разобрано вручную, не
теряют флаг при будущих правках YAML.

Run: python3 -m crawler.tests.test_deadline_semantics   (exit 1 on any failure)
"""
import os
import sys
import types

import yaml

# crawler.adapters.__init__ тянет spa → config.settings → pydantic_settings,
# которого локально нет. Самому адаптеру он не нужен: стабим по конвенции репо
# (см. test_query_retry) ДО первого импорта crawler.*.
if "pydantic_settings" not in sys.modules:
    _stub = types.ModuleType("pydantic_settings")

    class _BaseSettings(object):
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    _stub.BaseSettings = _BaseSettings
    sys.modules["pydantic_settings"] = _stub

from crawler.adapters.html import HtmlAdapter  # noqa: E402
from crawler.core.models import SourceConfig  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SOURCES = os.path.join(_REPO, "crawler", "config", "sources.yaml")

# Карточка ровно той формы, что отдаёт anorbank.uz
PAGE = """
<html><body>
  <div class="news_list_wrapper_items">
    <h3>ТЕХНИЧЕСКОЕ ЗАДАНИЕ На изготовление самоклеящихся наклеек формата A5</h3>
    <a class="news_list_wrapper_items_article" href="/about/press-center/tendery/nakleyki/"></a>
    <div class="news_list_wrapper_items_article-bottom"><span>19.07.2026</span></div>
  </div>
</body></html>
"""

BASE = {
    "id": "probe", "name": "Probe", "adapter": "html", "url": "https://x.uz/t/",
    "id_prefix": "probe",
    "html_selectors": {
        "container": "div.news_list_wrapper_items",
        "title": "h3",
        "deadline": "div.news_list_wrapper_items_article-bottom span",
        "link": "a.news_list_wrapper_items_article@href",
    },
}


def _parse(publication_flag):
    cfg = dict(BASE)
    cfg["html_selectors"] = dict(BASE["html_selectors"])
    if publication_flag:
        cfg["html_selectors"]["deadline_is_publication_date"] = True
    adapter = HtmlAdapter(SourceConfig(**cfg))
    return adapter._parse_page(PAGE, "https://x.uz/t/")


def test_flag_moves_date_out_of_deadline():
    items = _parse(True)
    assert len(items) == 1, len(items)
    t = items[0]
    assert t.deadline is None, "срок подачи должен остаться пустым, а не датой публикации: %r" % t.deadline
    assert t.date_start == "19.07.2026", t.date_start


def test_without_flag_behaviour_is_unchanged():
    """18 других HTML-источников не должны сдвинуться от этой правки."""
    items = _parse(False)
    assert len(items) == 1, len(items)
    t = items[0]
    assert t.deadline == "19.07.2026", t.deadline
    assert t.date_start is None, t.date_start


def test_empty_deadline_stays_empty_under_flag():
    """Флаг не должен изобретать дату там, где селектор ничего не нашёл."""
    cfg = dict(BASE)
    cfg["html_selectors"] = dict(BASE["html_selectors"])
    cfg["html_selectors"]["deadline"] = "div.no-such-class span"
    cfg["html_selectors"]["deadline_is_publication_date"] = True
    items = HtmlAdapter(SourceConfig(**cfg))._parse_page(PAGE, "https://x.uz/t/")
    assert len(items) == 1
    assert items[0].deadline is None and items[0].date_start is None


def test_none_deadline_survives_prefilter():
    """Смысл всей правки: пустой срок ПРОПУСКАЕТСЯ, а не режется."""
    from crawler.core.notifier import _is_deadline_expired
    from crawler.core.models import RawTender
    t = RawTender(id="x", external_id="x", title="x", organization="x",
                  source="Anor Bank", deadline=None)
    assert _is_deadline_expired(t) is False


# ── конфиги: разобранные вручную источники не должны терять флаг ──────────────
# Список пополняется по мере разбора. Источник сюда попадает ТОЛЬКО после
# проверки живых карточек — не по догадке про имя CSS-класса.
KNOWN_PUBLICATION_DATE = {
    "anorbank": "04.08, 4 живые карточки: срока подачи на странице лота нет вообще",
    "uzairports": "04.08, 10 карточек: буквально «Дата публикации: 03-08-2026»",
    "sqb": "04.08, 18 карточек: лента с временем «04.08.2026 09:08», строго по убыванию",
    "mobiuz": "04.08, 20 карточек: лента 04.08 / 03.08 / 31.07 по убыванию",
    "uzbekistonmet": "04.08, 20 карточек: в карточке одна дата, лента по убыванию; "
                     "селектор отдаёт дату и счётчик просмотров",
}

# saneg — тот же симптом, ДРУГАЯ причина: не дата публикации, а неточный
# селектор. Настоящий срок лежит в описании и только у части карточек, поэтому
# селектор обязан нести условие на текст. Без него `_parse_deadline` возьмёт
# последнюю дату и превратит «Дата объявления» в просроченный срок.
SANEG_DEADLINE_GUARD = "Дата завершения приема предложений"

# Пятеро банков на одной Bitrix-CMS. В карточке ДВА блока `div.post__date`:
# «Дата опубликования» и «Дата истечения». Голый `div.post__date span` берёт
# ПЕРВЫЙ, то есть публикацию — так и было у aab, и лот умирал через сутки.
# Условие на текст обязательно для всех пятерых.
BITRIX_BANK_CMS = ("aab", "aloqabank", "turonbank", "mkbank", "poytaxtbank")
BITRIX_DEADLINE_GUARD = "Дата истечения"

# Разобрано и НЕ является этим дефектом — держим, чтобы не «чинить» повторно:
#   trustbank   «Дата опубликования:16.07.2026Дата истечения:27.07.2026» — обе даты
#               в одной строке, _parse_deadline берёт ПОСЛЕДНЮЮ, то есть истечение
#   undp        «Application Deadline: 19-Aug-26» — настоящий срок
#   giz-tenders 05.03.2030 — настоящий срок
#   isdb        «5 September 2025» — настоящий срок (страница просто протухла)
# Открытые, но недоказанные (в аудите подозрительны, литерального признака нет):
#   uzbekistonmet, saneg (все даты одинаковы — похоже на неверный селектор),
#   uz-kor («03 августа 2026» — формат не парсится, дедлайн выходит пустым и
#   лот и так проходит), railway (селектор не совпадает, deadline=None).


def _sources():
    with open(_SOURCES, encoding="utf-8") as fh:
        return {s["id"]: s for s in yaml.safe_load(fh)["sources"]}


def test_known_publication_date_sources_keep_the_flag():
    srcs = _sources()
    missing = []
    for sid, why in KNOWN_PUBLICATION_DATE.items():
        s = srcs.get(sid)
        if s is None:
            missing.append((sid, "источник исчез из sources.yaml"))
            continue
        sel = s.get("html_selectors") or {}
        if not sel.get("deadline"):
            continue  # селектор убрали совсем — тоже допустимое решение
        if not sel.get("deadline_is_publication_date"):
            missing.append((sid, why))
    assert not missing, (
        "источник снова кладёт дату публикации в срок подачи — он замолчит "
        "и это будет выглядеть как «площадка ничего не публикует»: %s" % missing)


def test_bitrix_bank_cms_selects_the_expiry_not_the_publication_date():
    srcs = _sources()
    bad = []
    for sid in BITRIX_BANK_CMS:
        s = srcs.get(sid)
        if s is None:
            bad.append((sid, "источник исчез из sources.yaml"))
            continue
        sel = (s.get("html_selectors") or {}).get("deadline") or ""
        if BITRIX_DEADLINE_GUARD not in sel:
            bad.append((sid, sel))
    assert not bad, (
        "селектор берёт первый блок даты, то есть публикацию — лоты банка снова "
        "начнут умирать на гейте через сутки: %s" % bad)


def test_saneg_keeps_the_text_condition_on_its_deadline_selector():
    sel = ((_sources().get("saneg") or {}).get("html_selectors") or {}).get("deadline") or ""
    assert SANEG_DEADLINE_GUARD in sel, (
        "селектор saneg потерял условие на текст — он снова начнёт брать дату "
        "объявления за срок подачи и хоронить лоты: %r" % sel)


def test_flagged_sources_actually_have_a_deadline_selector():
    """Флаг без селектора — мусор в конфиге, ловим сразу."""
    bad = []
    for sid, s in _sources().items():
        sel = s.get("html_selectors") or {}
        if sel.get("deadline_is_publication_date") and not sel.get("deadline"):
            bad.append(sid)
    assert not bad, "deadline_is_publication_date без селектора deadline: %s" % bad


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as exc:
            print("FAIL", fn.__name__, exc)
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
