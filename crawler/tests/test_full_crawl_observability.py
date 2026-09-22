"""Contract checks for full-crawl logging and profile-aware health reporting."""
import ast
import io
import os
import sys
import types

if "crawler.config.settings" not in sys.modules:
    module = types.ModuleType("crawler.config.settings")
    module.settings = types.SimpleNamespace(
        supabase_url="", supabase_service_role_key="", telegram_bot_token="",
        telegram_alert_chat_id="", openrouter_api_key="", alert_keywords="",
        ai_score_threshold=70, ai_relevance_model="x", ai_relevance_model_fast="x",
    )
    sys.modules["crawler.config.settings"] = module

from crawler.core.crawl_logger import CrawlRunLogger
from crawler.core.runner import _record_adapter_result

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(relative):
    return io.open(os.path.join(_ROOT, relative), encoding="utf-8").read()


def test_runner_finalizes_in_a_finally_block_after_pipeline_errors():
    tree = ast.parse(_src("core/runner.py"))
    run_fn = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "run")
    guard = next(node for node in run_fn.body if isinstance(node, ast.Try))
    assert guard.finalbody, "crawl logger must finalize on an exception path"
    assert "finalize" in ast.unparse(guard.finalbody[0])
    assert "log_pipeline_error" in ast.unparse(guard.handlers[0])


def test_healthcheck_has_separate_full_api_component_and_exact_profile_match():
    source = _src("scripts/healthcheck.py")
    assert '"freshness.full_api"' in source
    assert 'set(row.get("source_filter") or []) == expected' in source
    assert "Latest full API crawl" in source


def test_adapter_last_error_is_recorded_even_when_rows_are_returned():
    adapter = types.SimpleNamespace(
        config=types.SimpleNamespace(id="partial-source"),
        last_error="detail endpoint returned 502",
        last_skipped_no_auth=False,
    )
    crawl_log = CrawlRunLogger(dry_run=True)
    crawl_log.log_source_start("partial-source")

    rows, outcome = _record_adapter_result(adapter, ["row-1"], crawl_log)

    assert rows == ["row-1"]
    assert outcome == {
        "count": 1, "skipped_no_auth": False,
        "error": "detail endpoint returned 502",
    }
    assert crawl_log.errors == ["[partial-source] detail endpoint returned 502"]
    assert crawl_log._source_stats["partial-source"].fetched == 1


def test_healthy_empty_adapter_result_does_not_create_an_error():
    adapter = types.SimpleNamespace(
        config=types.SimpleNamespace(id="healthy-empty"),
        last_error=None,
        last_skipped_no_auth=False,
    )
    crawl_log = CrawlRunLogger(dry_run=True)
    crawl_log.log_source_start("healthy-empty")

    rows, outcome = _record_adapter_result(adapter, [], crawl_log)

    assert rows == []
    assert outcome["count"] == 0
    assert outcome["error"] is None
    assert crawl_log.errors == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as exc:
            print("FAIL", fn.__name__, "%s: %s" % (type(exc).__name__, exc))
            failures += 1
    print("\n%d/%d passed" % (len(tests) - failures, len(tests)))
    raise SystemExit(1 if failures else 0)
