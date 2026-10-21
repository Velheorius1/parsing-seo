"""Счётчики, которые врали (аудит D, 21.10.2026).

ЧТО БЫЛО.
- crawl_runs.source_details[*].new — 0 у каждого источника в каждом прогоне:
  поле объявлено в SourceStats, но его никто не заполнял. Высох источник или нет —
  по нему было не понять.
- Сайт (Настройки краулера) показывал и давал править «Ключевые слова алертов»
  (15 слов) и «Минимальную цену» (10 млн) из crawler_settings, а краулер эти поля
  не читает: порог — константа 5 млн, словарь — ~160 слов в settings.py.

Здесь держится: новые строки раскладываются по источникам, неизвестный источник
не теряется молча; сайт не правит фильтры, которых краулер не видит.

Run: .venv/bin/python3 -m pytest crawler/tests/test_honest_counters.py -q
"""
import io
import os
from types import SimpleNamespace

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.core.crawl_logger import CrawlRunLogger  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _src(path):
    return io.open(os.path.join(_ROOT, path), encoding="utf-8").read()


def test_new_rows_are_counted_per_source():
    log = CrawlRunLogger(dry_run=True)
    for sid in ("etender", "humo"):
        log.log_source_start(sid)
        log.log_source_result(sid, 10)
    new = [SimpleNamespace(source="ETender UZEX")] * 3 + [SimpleNamespace(source="HUMO тендеры"),
                                                          SimpleNamespace(source="Чужой")]
    log.log_new_by_source(new, {"ETender UZEX": "etender", "HUMO тендеры": "humo"})
    details = {sid: s.to_dict()["new"] for sid, s in log._source_stats.items()}
    assert details == {"etender": 3, "humo": 1}
    assert log.new_unmapped == 1


def test_runner_feeds_the_per_source_counter_after_upsert():
    src = _src("crawler/core/runner.py")
    i = src.index("crawl_log.log_upsert(")
    assert "crawl_log.log_new_by_source(new_tenders" in src[i:i + 300]


def test_site_does_not_edit_filters_the_crawler_never_reads():
    tsx = _src("web/src/components/tenders/CrawlerSettings.tsx")
    assert "saveSetting('min_price'" not in tsx
    assert "saveSetting('alert_keywords'" not in tsx
    assert "5 000 000" in tsx
