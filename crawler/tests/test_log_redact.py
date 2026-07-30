"""Пины редакции секретов в логах (30.07).

Токен бота лежал в 3 227 строках /var/log/parsing-seo-*.log, потому что httpx
печатает полный URL, а Telegram носит токен в пути. Тесты держат две вещи:
секрет действительно вырезается (включая случай, когда URL приехал в args, а не
в тексте сообщения), и полезный сигнал при этом остаётся.

Run: python3 -m crawler.tests.test_log_redact   (exit 1 on any failure)
"""
import logging
import sys

from crawler.core.log_redact import install, redact

TOKEN = "8428686622:AAHj1H1DUO1_ata-GDtNok3gIOP0B0cJXt4"
URL = "https://api.telegram.org/bot%s/sendMessage" % TOKEN


def test_token_is_cut_from_url():
    out = redact("HTTP Request: POST %s \"HTTP/1.1 200 OK\"" % URL)
    assert TOKEN not in out, out
    assert "/bot<TOKEN>/sendMessage" in out, out


def test_useful_signal_survives():
    out = redact("HTTP Request: POST %s \"HTTP/1.1 200 OK\"" % URL)
    assert "api.telegram.org" in out and "200 OK" in out, out


def test_query_secrets_are_cut():
    out = redact("GET https://x.uz/api?apikey=abc123&page=2")
    assert "abc123" not in out and "page=2" in out, out
    out2 = redact("GET https://x.uz/a?access_token=zzz999")
    assert "zzz999" not in out2, out2


def test_redaction_is_idempotent():
    once = redact("POST %s" % URL)
    assert redact(once) == once


def test_ordinary_text_untouched():
    for s in ("робот собрал 100 строк", "bot вернул 0", "/bots/list"):
        assert redact(s) == s, s


def test_install_is_idempotent():
    install()
    assert install() is False


def test_record_args_are_redacted_not_just_message():
    # Именно этот случай и течёт в проде: httpx кладёт URL в args, а в msg
    # держит «HTTP Request: %s %s "%s"». Редакция только msg не спасла бы.
    install()
    rec = logging.getLogRecordFactory()(
        "httpx", logging.INFO, __file__, 1,
        "HTTP Request: %s %s \"%s\"", ("POST", URL, "HTTP/1.1 200 OK"), None)
    assert TOKEN not in rec.getMessage(), rec.getMessage()
    assert "200 OK" in rec.getMessage()


def test_dict_args_are_redacted():
    install()
    # logging принимает словарь именно так — одним элементом кортежа, и сам его
    # разворачивает в record.args; передать словарь напрямую нельзя.
    rec = logging.getLogRecordFactory()(
        "x", logging.INFO, __file__, 1, "%(url)s", ({"url": URL},), None)
    assert TOKEN not in rec.getMessage(), rec.getMessage()


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
