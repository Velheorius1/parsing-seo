"""Пины чтения ТЗ разборщиком лотов (05.08).

Случай, из которого это выросло — лот 506231 (XALQ BANK, картхолдеры для
Mastercard, 1,23 млрд). Категория площадки — «Кожа и изделия из кожи», и
разборщик выдал «ПРОПУСТИТЬ: кожевенное производство, не наш профиль». В ТЗ
при этом прямым текстом квалификационное требование: опыт производства
«kardholder yoki bank kartalari qadoqlari yoki kartonli premium qadoqlash» —
то есть КАРТОН, наш профиль. Документы разборщик не читал вовсе.

Свойства, которые тут держатся:
  • неудача извлечения текста возвращает None, а не пустую строку: пустую
    модель прочтёт как «в ТЗ ничего нет» и снова поверит категории;
  • любой отказной путь ГОВОРИТ, что материал не подтверждён — молчаливого
    «нет данных» быть не должно;
  • правило про приоритет ТЗ над категорией живёт в системном промпте;
  • инструмент объявлен модели и разведён в диспетчере вызовов.

Run: python3 -m crawler.tests.test_investigator_docs   (exit 1 on any failure)
"""
import asyncio
import os
import sys
import types


def _load():
    cfg = "crawler.config.settings"
    if cfg not in sys.modules:
        m = types.ModuleType(cfg)
        m.settings = types.SimpleNamespace(openrouter_api_key="",
                                           telegram_bot_token="",
                                           telegram_alert_chat_id="",
                                           supabase_url="",
                                           supabase_service_role_key="")
        sys.modules[cfg] = m
    import crawler.scripts.investigator as I
    return I


I = _load()


def _tender(**kw):
    base = {"source": "ETender Обсуждения",
            "source_url": "https://etender.uzex.uz/lot/506231"}
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_pdf_extract_failure_returns_none_not_empty():
    """None и '' — разные ответы: '' читается как «в ТЗ пусто»."""
    assert I._pdf_to_text(b"not a pdf at all") is None


def test_unsupported_source_says_material_unconfirmed():
    out = asyncio.run(I._tool_fetch_documents(_tender(source="Anor Bank")))
    assert "не подтверждён" in out.lower(), out


def test_unparseable_url_says_documents_not_read():
    out = asyncio.run(I._tool_fetch_documents(
        _tender(source_url="https://etender.uzex.uz/")))
    assert "не прочитаны" in out.lower(), out


def test_tool_is_declared_to_the_model():
    names = [t["function"]["name"] for t in I.TOOLS]
    assert "fetch_lot_documents" in names, names


def test_tool_is_wired_in_the_dispatcher():
    """Объявить мало — вызов должен быть разведён, иначе «unknown tool»."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(I.__file__))), "scripts", "investigator.py"),
        encoding="utf-8").read()
    assert 'fn == "fetch_lot_documents"' in src
    assert "_tool_fetch_documents(tender)" in src


def test_prompt_puts_tz_above_platform_category():
    s = I.SYSTEM
    assert "категори" in s.lower()
    assert "ТЗ" in s
    assert "506231" in s, "живой случай должен остаться в промпте как якорь"
    assert "уточнить" in s, "без ТЗ вердикт должен быть «уточнить», а не «пропустить»"


def test_download_uses_the_verified_endpoint():
    """GET и POST с телом дают 405/500 — работает только POST с query."""
    assert I._ETENDER_FILE_API.endswith("/api/common/DownloadFile")
    src = open(I.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    assert "cl.post(_ETENDER_FILE_API, params={" in src


def test_non_pdf_attachment_is_reported_not_skipped():
    """RAR/DOCX пропускаем, но обязаны сказать об этом вслух."""
    src = open(I.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    assert "не PDF, текст НЕ извлечён" in src


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
