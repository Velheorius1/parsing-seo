"""Пин: каждый вызов OpenRouter выключает reasoning (04.08).

Зачем этот тест существует. Правило «reasoning MUST stay disabled» записано в
error-log 06-29 и продублировано комментариями в notifier, investigator,
source_scout и shadow_search — но четыре вызова его не выполняли, и заметить
это было нечем: все четыре при пустом ответе МОЛЧА возвращают None/False и
падают на запасной путь. Замер 04.08 на живых данных:

    playbook_refine.distil   3/3 пусто (и на прежней pro тоже) → playbook не
                             учился вообще: 358 коррекций за 30 дней в мусор
    enricher._enrich_one     3/3 пусто на flash-0731 (на pro и flash работало)
    audit_quality            3/3 пусто на flash-0731 (на pro и flash работало)
    ai_evaluator             работал, но рассуждение съедало 277-695 из 800

Расширение max_tokens не лечит: на бюджете 2000 рассуждение съедает и 2000.

Проверка статическая — по исходникам, а не по сети: тест должен работать без
ключа OpenRouter и без денег. Он ловит ровно ту ошибку, которая случилась —
новый (или отредактированный) вызов без флага.

Run: python3 -m crawler.tests.test_reasoning_disabled   (exit 1 on any failure)
"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ENDPOINT = "openrouter.ai/api/v1/chat/completions"
# Флаг может быть записан как "reasoning": {"enabled": False} с любыми пробелами
# и переносами — сравниваем по нормализованному тексту.
_FLAG = re.compile(r'"reasoning"\s*:\s*\{\s*"enabled"\s*:\s*False\s*\}')


def _python_sources():
    out = []
    for dirpath, dirnames, filenames in os.walk(_ROOT):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "tests")]
        for name in filenames:
            # `._foo.py` — не исходник, а ресурсная вилка AppleDouble от scp с Мака:
            # бинарь с расширением .py. На проде их лежало 59 штук от 16.03, и эта
            # проверка падала на них UnicodeDecodeError — то есть флаг reasoning:false
            # на проде не проверялся вообще ни разу с того дня.
            if name.endswith(".py") and not name.startswith("._"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def _call_sites():
    """[(путь, номер строки, текст тела вызова)] для каждого обращения к OpenRouter."""
    sites = []
    for path in _python_sources():
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        for i, line in enumerate(lines):
            if _ENDPOINT not in line:
                continue
            # Тело запроса лежит НИЖЕ строки с URL: httpx.post(url, headers=..., json={...}).
            # 40 строк с запасом перекрывают самый развесистый из наших вызовов.
            body = "".join(lines[i:i + 40])
            sites.append((os.path.relpath(path, _ROOT), i + 1, body))
    return sites


def test_call_sites_are_found():
    """Сам тест не должен молча деградировать до нуля проверок."""
    sites = _call_sites()
    assert len(sites) >= 8, "нашлось всего %d вызовов OpenRouter — проверь обход" % len(sites)


def test_every_openrouter_call_disables_reasoning():
    missing = [(p, ln) for p, ln, body in _call_sites() if not _FLAG.search(body)]
    assert not missing, (
        "вызов OpenRouter без reasoning:{enabled:False} — рассуждающая модель съест "
        "весь бюджет и вернёт пустой ответ МОЛЧА: %s" % missing)


def test_flag_regex_matches_real_formatting():
    """Регекс не должен пропускать реальные способы записи флага."""
    assert _FLAG.search('"reasoning": {"enabled": False}')
    assert _FLAG.search('"reasoning":{"enabled":False}')
    assert _FLAG.search('"reasoning": {\n    "enabled": False\n}')
    assert not _FLAG.search('"reasoning": {"enabled": True}')
    assert not _FLAG.search('"max_tokens": 600')


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
