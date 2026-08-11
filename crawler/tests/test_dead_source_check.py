"""Пины проверки мёртвых источников в healthcheck (10.08).

Из чего выросло. Проверка «источник включён, но за 7 дней ноль строк» тащила в
питон всю таблицу: постраничный обход `tenders` по `collected_at` за неделю, с
растущим offset и капом 200 тыс. строк. Под условие подходит почти вся активная
таблица — строки перечитываются каждым проходом краулера, — то есть обход шёл на
сотни страниц с глубокими offset'ами. 10.08 он живьём упёрся в `57014` три ретрая
подряд, и проверка отчиталась WARN «Dead-source check failed».

Чем это опасно именно здесь: отказ и находка выглядят ОДИНАКОВО (оба WARN). Жёлтая
лампочка «проверка не сработала» неотличима от жёлтой лампочки «источник умер», и
слепота маскируется под работу. Кап на 200 тыс. добавлял второй способ соврать —
молча обрезать выборку и объявить живым источник, чьи строки не попали в окно.

Теперь то же самое берётся одной серверной агрегацией `source_freshness()`:
«ноль строк за неделю» ≡ «последняя строка старше недели».

Свойства, которые тут держатся:
  • источника нет в выдаче вовсе -> мёртвый (а не «нет данных, значит ладно»);
  • нечитаемая дата -> мёртвый, то есть шумим, а не молчим;
  • метки времени сравниваются ДАТАМИ: 'Z' и '+00:00' — один момент, но 'Z'
    лексикографически больше, и живой источник стал бы мёртвым из-за формы записи;
  • обхода таблицы постранично в этой проверке больше нет.

Run: python3 -m crawler.tests.test_dead_source_check   (exit 1 on any failure)
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "scripts", "healthcheck.py")


def _body():
    """Тело check_dead_sources — читаем текстом: модуль тянет прод-зависимости."""
    s = open(_SRC, encoding="utf-8").read()
    i = s.find("def check_dead_sources")
    assert i > 0, "проверка мёртвых источников исчезла"
    j = s.find("\n    # ── Check 14", i)
    return s[i:j if j > i else len(s)]


def test_check_uses_the_server_side_aggregate():
    b = _body()
    assert "rpc('source_freshness')" in b or 'rpc("source_freshness")' in b, b[:200]


def _code_only(text):
    """Только код: слово «offset» законно живёт в комментарии про то, как было."""
    out = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        out.append(line.split("  #")[0])
    return "\n".join(out)


def test_no_table_pagination_left_in_this_check():
    """Именно она и падала в 57014."""
    b = _code_only(_body())
    assert ".range(" not in b, "постраничный обход вернулся"
    assert "offset" not in b.lower(), "остались следы обхода с offset"


def test_no_silent_cap():
    """Кап молча обрезал выборку и делал мёртвый источник живым."""
    b = _body()
    assert "200000" not in b


def test_timestamps_are_compared_as_dates_not_strings():
    b = _body()
    assert "fromisoformat" in b, "сравнение строками ломается на 'Z' против '+00:00'"


def test_string_comparison_of_timestamps_gives_the_wrong_answer():
    """Контрпример, а не вера: строки и даты расходятся в вердикте.

    Порог `datetime.isoformat()` несёт микросекунды, а метка из базы может прийти
    без дробной части и с 'Z'. Тогда 'Z' (0x5A) сравнивается с '.' (0x2E) — и
    источник, который на долю секунды СТАРШЕ порога, строкой выглядит свежее.
    """
    cutoff_dt = datetime(2026, 8, 3, 14, 0, 0, 123456, tzinfo=timezone.utc)
    last_dt = datetime(2026, 8, 3, 14, 0, 0, 0, tzinfo=timezone.utc)   # старше порога
    cutoff_s = cutoff_dt.isoformat()                       # ...14:00:00.123456+00:00
    last_s = last_dt.isoformat().replace("+00:00", "Z")    # ...14:00:00Z

    assert last_dt < cutoff_dt, "по датам метка старше порога — источник мёртв"
    assert last_s > cutoff_s, "а строкой она больше порога — выглядела бы живой"
    # то есть два способа сравнения дают ПРОТИВОПОЛОЖНЫЕ вердикты
    assert (last_dt < cutoff_dt) != (last_s < cutoff_s)


def test_missing_source_counts_as_dead():
    b = _body()
    assert "if last is None" in b or "if not last" in b
    assert "return True" in b


def test_no_check_in_healthcheck_paginates_the_tenders_table_anymore():
    """Обе проверки болели одним и тем же; вылечить одну — оставить мину.

    `sources.dead_7d` падала совсем, `sources` дотягивала только с ретраями на
    каждой странице. Разница лишь в том, сколько строк успело накопиться.
    """
    s = open(_SRC, encoding="utf-8").read()
    assert ".range(" not in s, "постраничный обход таблицы вернулся в healthcheck"
    assert "200000" not in s, "молчаливый кап вернулся"


def test_source_counts_come_from_the_aggregate():
    s = open(_SRC, encoding="utf-8").read()
    i = s.find("def check_sources")
    body = s[i:s.find("\n    # ── Check 4", i)]
    assert "cnt_7d" in body, "недельный счётчик должен приходить из source_freshness()"


def test_failure_and_finding_are_still_distinguishable_in_text():
    """Отказ проверки обязан называть себя отказом, а не сливаться с находкой."""
    s = open(_SRC, encoding="utf-8").read()
    assert "Dead-source check failed" in s


def test_missing_rpc_field_is_a_broken_instrument_not_an_outage():
    """Регрессия, внесённая 10.08 и пойманная независимой оценкой 11.08.

    Перевод check_sources на source_freshness() создал путь, где отсутствие поля
    cnt_7d (миграция не применена) давало FAIL «No tenders collected in last 7
    days» — то есть отказ ПРИБОРА выглядел как остановка сбора. Ровно от такого
    ложного FAIL 21.07 уже уходили, добавив WARN «transient?». Пин на то, что
    два случая различаются в коде."""
    b = _body_of("check_sources")
    assert "cnt_7d" in b and "миграция" in b.lower(), b[:200]
    assert b.index("WARN") < b.index('FAIL, "No tenders'), \
        "ветка отказа прибора должна стоять ДО ветки настоящей остановки"


def _body_of(fn):
    s = open(_SRC, encoding="utf-8").read()
    i = s.find("def %s" % fn)
    j = s.find("\n    # ── Check", i + 10)
    return s[i:j if j > i else len(s)]


# --- белый список с причинами (11.08) ---------------------------------------

def _whitelist_block():
    s = open(_SRC, encoding="utf-8").read()
    i = s.index("DEAD_SOURCES_WHITELIST = {")
    j = s.index("\n        }", i)
    return s[i:j]


def test_whitelist_is_a_mapping_with_reasons_not_a_bare_set():
    """До 11.08 это было множество имён с припиской «пересматривать ежеквартально»
    — пересматривать было НЕ ПО ЧЕМУ: ни даты, ни замера, ни автора. Такой список
    растёт и молча глушит сторожа."""
    b = _whitelist_block()
    assert "': _LEGACY" in b or "': '" in b, "белый список снова стал множеством"
    assert b.count(":") > 30, "у записей должны быть причины"


def test_todays_seven_are_listed_with_a_dated_reason():
    """Каждая из семи разобранных 11.08 записей обязана нести дату и факт."""
    b = _whitelist_block()
    for name in ("OSCE Uzbekistan", "TG: Beeline Tenders", "TG: UNDP UZB Tenders",
                 "Хамкорбанк", "TenderWeek.com", "Tashkent Steel (ТМЗ)", "Ипотека-банк"):
        assert "'%s'" % name in b, name
    assert b.count("(11.08)") >= 7, "у каждой новой записи должна быть дата"


def test_broken_sources_say_they_are_broken():
    """Два источника внесены СЛОМАННЫМИ — решение не чинить принято по экономике.
    Причина обязана говорить это прямо, иначе через полгода прочитается как
    «источник просто тихий»."""
    b = _whitelist_block()
    assert "СЛОМАН У НАС" in b and "СЛОМАНА СВЯЗЬ" in b, b[:200]


def test_excused_sources_stay_visible():
    """Белый список гасит тревогу, но не факт. Иначе он — чёрная дыра: источник
    умер, объяснение устарело, увидеть неоткуда."""
    b = _body()
    assert "dead_excused" in b, "нет строки со счётчиком объяснённых"
    assert "excused" in b


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
