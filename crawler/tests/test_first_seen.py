"""Пины измерения запаса до дедлайна (10.08).

Из чего выросло: единственным показателем здоровья конвейера было «18-20 алертов
в день». Он не отличает «пришли за неделю до дедлайна» от «пришли за час», хотя
между этими случаями разница в том, можно ли вообще участвовать. Считать запас
стало возможно, когда выяснилось, что `created_at` — вставка (upsert его не
трогает), а не «последний раз видели», как было записано в main.md.

Свойства, которые тут держатся:
  • дедлайн ровно в 00:00 читается как КОНЕЦ дня — иначе запас систематически
    занижается почти на сутки на каждом источнике с датой без времени;
  • время площадок — Asia/Tashkent (UTC+5); сравнение с created_at (UTC) без
    приведения дало бы ровно пятичасовую ошибку в одну сторону;
  • источники, у которых в поле дедлайна лежит дата публикации, ИСКЛЮЧАЮТСЯ —
    там «запас» был бы фикцией (класс дефекта разобран 09.08 на Anor Bank);
  • пустая выборка даёт None, а не 0: ноль здесь читался бы как «в обрез»;
  • «уже закрыт» считается по строгому знаку, и деньги в этот счётчик попадают
    только у реально просроченных.

Run: python3 -m crawler.tests.test_first_seen   (exit 1 on any failure)
"""
import sys
import types
from datetime import datetime, timedelta, timezone

if "crawler.config.settings" not in sys.modules:
    _m = types.ModuleType("crawler.config.settings")
    _m.settings = types.SimpleNamespace(
        telegram_bot_token="", telegram_alert_chat_id="", openrouter_api_key="",
        alert_keywords="", ai_score_threshold=70,
        ai_relevance_model="x", ai_relevance_model_fast="x",
        supabase_url="", supabase_service_role_key="",
    )
    sys.modules["crawler.config.settings"] = _m

import crawler.scripts.first_seen_report as R

UTC = timezone.utc


# --- разбор дедлайна --------------------------------------------------------

def test_midnight_deadline_means_end_of_that_day():
    """«срок: 12.08» человек читает как весь день 12-го, и продовый гейт тоже."""
    got = R.deadline_utc("2026-08-12")
    assert got is not None
    local = got.astimezone(R._TASHKENT)
    assert (local.hour, local.minute) == (23, 59), local


def test_deadline_is_tashkent_local_time():
    """UTC+5 без перехода: конец дня 12.08 в Ташкенте — 18:59:59 UTC того же дня.
    Без приведения зоны запас врал бы ровно на пять часов в одну сторону."""
    got = R.deadline_utc("2026-08-12")
    assert got.tzinfo is not None
    assert got == datetime(2026, 8, 12, 18, 59, 59, tzinfo=UTC), got


def test_deadline_granularity_is_one_day():
    """Площадки отдают дату без времени, и продовый _parse_deadline время не
    разбирает вовсе (_DATE_PATTERNS — только даты). Значит точность измерения —
    сутки, и «меньше суток» читается как «увидели в день дедлайна», а не «за час».
    Пин, чтобы это свойство не потерялось, если формат данных изменится."""
    with_time = R.deadline_utc("2026-08-12 14:00")
    plain = R.deadline_utc("2026-08-12")
    assert with_time == plain, (with_time, plain)


def test_unparseable_deadline_is_none_not_zero():
    for bad in (None, "", "как договоримся"):
        assert R.deadline_utc(bad) is None, bad


def test_hours_left_is_signed():
    created = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)
    assert R.hours_left("2026-08-12 14:00", created) > 0
    assert R.hours_left("2026-08-09 14:00", created) < 0


def test_hours_left_needs_both_sides():
    assert R.hours_left("2026-08-12", None) is None
    assert R.hours_left(None, datetime.now(UTC)) is None


# --- раскладка по корзинам --------------------------------------------------

def test_bucket_boundaries():
    assert R.bucket_of(-0.1) == "уже закрыт"
    assert R.bucket_of(0.0) == "меньше суток"
    assert R.bucket_of(23.9) == "меньше суток"
    assert R.bucket_of(24.0) == "1-3 дня"
    assert R.bucket_of(71.9) == "1-3 дня"
    assert R.bucket_of(72.0) == "3-7 дней"
    assert R.bucket_of(168.0) == "больше недели"
    assert R.bucket_of(10000.0) == "больше недели"


def test_every_value_lands_in_exactly_one_bucket():
    for h in (-500, -1, 0, 1, 23, 24, 71, 72, 167, 168, 999):
        assert R.bucket_of(float(h)) in [b[0] for b in R._BUCKETS], h


# --- перцентили -------------------------------------------------------------

def test_percentile_of_empty_is_none_not_zero():
    assert R.percentile([], 50) is None


def test_percentile_basic():
    xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert R.percentile(xs, 50) in (5, 6)
    assert R.percentile(xs, 10) in (1, 2)
    assert R.percentile(xs, 90) in (9, 10)
    assert R.percentile([42], 50) == 42


# --- разбор меток времени ---------------------------------------------------

def test_parse_ts_handles_postgres_shapes():
    """Postgres отдаёт дробную часть разной длины — и с Z, и со смещением."""
    want = datetime(2026, 8, 10, 5, 30, tzinfo=UTC)
    for s in ("2026-08-10T05:30:00Z", "2026-08-10T05:30:00+00:00",
              "2026-08-10T05:30:00.123456+00:00", "2026-08-10T05:30:00.12+00:00",
              "2026-08-10T05:30:00.123456789+00:00"):
        got = R._parse_ts(s)
        assert got is not None and got.replace(microsecond=0) == want, (s, got)


def test_parse_ts_assumes_utc_when_naive():
    got = R._parse_ts("2026-08-10T05:30:00")
    assert got is not None and got.tzinfo is not None


def test_parse_ts_of_garbage_is_none():
    assert R._parse_ts("вчера") is None
    assert R._parse_ts("") is None


# --- агрегация --------------------------------------------------------------

def _row(src="X", dl="2026-08-20", created="2026-08-10T00:00:00Z", price=0):
    return {"source": src, "deadline": dl, "created_at": created, "price": price}


def test_publication_date_sources_are_excluded_not_measured():
    rows = [_row(src="ПлохойИсточник"), _row(src="Хороший")]
    st = R.summarize(rows, {"ПлохойИсточник"})
    assert st["excluded"] == 1 and st["measured"] == 1, st


def test_unparseable_rows_are_counted_separately():
    st = R.summarize([_row(dl="когда-нибудь")], set())
    assert st["unparsed"] == 1 and st["measured"] == 0, st


def test_money_counts_only_for_lots_already_closed():
    rows = [_row(dl="2026-08-01", price=1e9),      # закрыт до появления
            _row(dl="2026-08-20", price=5e9)]      # ещё живой
    st = R.summarize(rows, set())
    assert st["buckets"].get("уже закрыт") == 1
    assert abs(st["late_price"] - 1e9) < 1, st["late_price"]


def test_source_breakdown_tracks_late_count():
    rows = [_row(src="A", dl="2026-08-01"), _row(src="A", dl="2026-08-20"),
            _row(src="B", dl="2026-08-20")]
    st = R.summarize(rows, set())
    assert st["by_source"]["A"]["n"] == 2 and st["by_source"]["A"]["late"] == 1
    assert st["by_source"]["B"]["late"] == 0


def test_counts_add_up():
    """Каждая строка ровно в одной категории — иначе проценты врут."""
    rows = [_row(src="skip"), _row(dl="нет даты"), _row(), _row()]
    st = R.summarize(rows, {"skip"})
    assert st["excluded"] + st["unparsed"] + st["measured"] == st["seen"] == 4, st
    assert sum(st["buckets"].values()) == st["measured"]


def test_empty_input_gives_none_percentiles():
    st = R.finalize(R.summarize([], set()))
    assert st["measured"] == 0 and st["p50"] is None


def test_streaming_accumulates_across_pages():
    """Выборка за месяц приходит страницами и целиком в память не кладётся —
    накопитель обязан переживать несколько вызовов (иначе счёт будет по
    последней странице, а отчёт этого не покажет)."""
    st = R.new_stats()
    R.summarize([_row(), _row()], set(), st)
    R.summarize([_row(dl="2026-08-01")], set(), st)
    R.finalize(st)
    assert st["seen"] == 3 and st["measured"] == 3, st
    assert st["buckets"].get("уже закрыт") == 1
    assert st["p50"] is not None


def test_lag_accumulates_across_pages():
    page = [{"source": "A", "created_at": "2026-08-10T08:59:00Z",
             "extra_info": {"Опубликовано": "10.08.2026 10:59"}}]
    lag = R.new_lag()
    R.summarize_lag(page, lag)
    R.summarize_lag(page, lag)
    R.finalize_lag(lag)
    assert lag["n"] == 2 and lag["p50"] is not None, lag


# --- задержка обнаружения ---------------------------------------------------

def test_published_time_is_read_as_tashkent_local():
    """'10.08.2026 10:59' — местное время площадки, 05:59 UTC."""
    got = R.published_utc({"Опубликовано": "10.08.2026 10:59"})
    assert got == datetime(2026, 8, 10, 5, 59, tzinfo=UTC), got


def test_missing_or_broken_publication_time_is_none():
    for ei in (None, {}, {"Опубликовано": ""}, {"Опубликовано": "вчера"}, "не словарь"):
        assert R.published_utc(ei) is None, ei


def test_detection_lag_is_measured_from_publication():
    created = datetime(2026, 8, 10, 8, 59, tzinfo=UTC)   # 13:59 по Ташкенту
    lag = R.detection_lag_hours({"Опубликовано": "10.08.2026 10:59"}, created)
    assert abs(lag - 3.0) < 0.01, lag


def test_impossible_negative_lag_is_dropped_not_reported():
    """Найти раньше публикации нельзя — такое значение означает расхождение
    часов или формата, и в среднее оно попадать не должно."""
    created = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
    assert R.detection_lag_hours({"Опубликовано": "10.08.2026 23:00"}, created) is None


def test_lag_summary_counts_only_rows_that_have_a_publication_time():
    rows = [{"source": "A", "created_at": "2026-08-10T08:59:00Z",
             "extra_info": {"Опубликовано": "10.08.2026 10:59"}},
            {"source": "B", "created_at": "2026-08-10T08:59:00Z", "extra_info": {}},
            {"source": "A", "created_at": "2026-08-10T00:00:00Z",
             "extra_info": {"Опубликовано": "10.08.2026 23:00"}}]  # невозможный
    lag = R.summarize_lag(rows)
    assert lag["n"] == 1 and lag["negative"] == 1, lag
    assert list(lag["by_source"]) == ["A"], lag["by_source"]


# --- связь с реальным конфигом ----------------------------------------------

def test_exclusion_list_is_actually_wired_to_sources_yaml():
    """Если бы список читался мимо конфига, отчёт молча мерил бы даты публикации."""
    got = R.publication_date_sources()
    assert isinstance(got, set) and got, "в sources.yaml есть такие источники — их нашли"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as exc:  # падение — тоже провал, а не остановка прогона
            print("FAIL", fn.__name__, "%s: %s" % (type(exc).__name__, exc))
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
