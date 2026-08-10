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


def test_archive_and_docx_are_read_not_skipped():
    """06.08: RAR и DOCX больше не пропускаем — у лота 506231 настоящее
    техзадание лежало ИМЕННО в архиве, рядом с узбекским DOCX на 38 тыс. знаков."""
    src = open(I.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    assert "_archive_to_text" in src and "_docx_to_text" in src
    assert 'b"Rar!"' in src, "RAR должен опознаваться и по сигнатуре, не только по ext"


def test_unknown_format_is_reported_not_silently_dropped():
    """Чего не прочитали — про то говорим вслух, а не молчим."""
    src = open(I.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    assert "формат не читаем, содержимое не проверено" in src


def test_docx_extraction_works_on_a_real_docx():
    """DOCX читаем без внешних зависимостей — это zip с word/document.xml."""
    import io as _io
    import zipfile
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml",
                   "<w:document><w:body><w:p><w:r><w:t>kartonli qadoq</w:t>"
                   "</w:r></w:p></w:body></w:document>")
    got = I._docx_to_text(buf.getvalue())
    assert got and "kartonli qadoq" in got, got


def test_broken_docx_returns_none():
    assert I._docx_to_text(b"not a zip") is None


def test_scanned_pdf_triggers_ocr_and_says_so():
    """Скан без текстового слоя — самый опасный случай: pdftotext отдаёт пустоту,
    и это читается как «в ТЗ ничего нет». У лота 506231 именно в скане лежали
    «offset bosma», CMYK+Pantone и 3D UV лак."""
    src = open(I.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    assert "_ocr_pdf" in src and "tesseract" in src
    assert "распознано OCR" in src, "пользователь должен знать, что текст распознан"
    assert I._TEXT_LAYER_MIN > 0


def test_missing_external_tool_is_not_a_crash():
    """Нет утилиты в системе — код 127, а не исключение наружу."""
    rc, out = I._run(["definitely-not-installed-xyz"], timeout=5)
    assert rc == 127 and out == b""


def test_ocr_has_bounded_cost():
    """Один плохой скан не должен съесть весь разбор.

    На прод-прогоне 10.08 страницы 2-4 упирались в таймаут по 180с — девять
    минут на документ. Бюджеты и однопоточность ограничивают ущерб независимо
    от того, была причина в нагрузке или в самом файле.
    """
    src = open(I.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    assert I._OCR_PAGE_TIMEOUT <= 90, I._OCR_PAGE_TIMEOUT
    assert I._OCR_TOTAL_BUDGET <= 300, I._OCR_TOTAL_BUDGET
    assert "OMP_THREAD_LIMIT" in src
    assert "бюджет" in src, "исчерпание бюджета должно попадать в лог, а не молчать"


def test_env_extra_is_passed_through():
    rc, out = I._run(["sh", "-c", "echo $OMP_THREAD_LIMIT"], timeout=10,
                     env_extra={"OMP_THREAD_LIMIT": "1"})
    assert rc == 0 and out.strip() == b"1", (rc, out)


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
