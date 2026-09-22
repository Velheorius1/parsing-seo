"""Contract checks for full-crawl logging and profile-aware health reporting."""
import ast
import io
import os

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
