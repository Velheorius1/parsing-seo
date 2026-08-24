"""Пины ссылок Cooperation в оповещениях (24.08).

Из чего выросло. Данияр: «мне не удобно вообще проходить по ссылке — там
ничего не видно». Проверено кликами с трёх сторон (чистый десктопный браузер,
айфонный UA через резидентный прокси, наш архив):

  • `supplier/lots?lotId=` отдаёт 404 УЖЕ И НА ДЕСКТОПЕ — площадка обновилась
    после записи 05.08 про «инертный параметр»;
  • на телефоне ЛЮБАЯ coop-ссылка (включая «рабочие» планы) редиректится на
    mobile.cooperation.uz — заглушку мобильного приложения. Telegram читается
    с телефона, значит платформенная ссылка мертва всегда;
  • напоминания о дедлайнах слали голый source_url — единственный канал, куда
    не дошла link-стратегия пушей; битая ссылка со скриншота Данияра — оттуда;
  • в дайджесте у битых SPA ссылки не было ВООБЩЕ: платформенную код убирал,
    архивную взамен не ставил.

Свойства, которые тут держатся:
  • у Cooperation архив ПЕРВЫМ во всех трёх каналах: пуш, дайджест, напоминание;
  • платформенная ссылка не исчезает молча — остаётся с пометкой «с десктопа»;
  • обычные источники не задеты: у них порядок прежний;
  • offer-join ищет оферту ПО НОМЕРУ (точный поиск, total=1), а не по первым
    словам названия среди 419 тысяч записей (это и давало цену у 2% строк).

Run: python3 -m crawler.tests.test_coop_links   (exit 1 on any failure)
"""
import io
import os
import sys
import types

if "crawler.config.settings" not in sys.modules:
    _m = types.ModuleType("crawler.config.settings")
    _m.settings = types.SimpleNamespace(
        telegram_bot_token="", telegram_alert_chat_id="", openrouter_api_key="",
        alert_keywords="", ai_score_threshold=70,
        ai_relevance_model="x", ai_relevance_model_fast="x",
        supabase_url="", supabase_service_role_key="",
    )
    sys.modules["crawler.config.settings"] = _m

from crawler.core import notifier as N
from crawler.core import deadline_tracker as D
from crawler.core.models import RawTender

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
_VERCEL = "https://parsing-seo.vercel.app/tenders"


def _t(source, url, title="Печать буклетов"):
    return RawTender(id="x", external_id="x", title=title, organization="O",
                     price=20_000_000.0, currency="UZS", source=source, source_url=url)


# --- пуш ---------------------------------------------------------------------

def test_push_plan_archive_comes_before_platform():
    """«Рабочий» план: на десктопе ссылка открывается, на телефоне — заглушка
    приложения. Архив первым, платформа пометкой."""
    t = _t("Cooperation.uz Закупочные планы (filtered)",
           "https://new.cooperation.uz/plan-schedule/abc")
    txt = N._format_alert(t, "печать", alert_seq=1, db_id="U-1")
    assert "%s/U-1" % _VERCEL in txt
    assert txt.index("U-1") < txt.index("plan-schedule"), "архив должен стоять раньше платформы"
    assert "с десктопа" in txt


def test_push_lot_never_links_the_dead_route():
    """`supplier/lots?lotId=` — 404 на десктопе, заглушка на телефоне.
    В пуше этой ссылки быть не должно вовсе."""
    t = _t("Cooperation.uz Лоты", "https://new.cooperation.uz/supplier/lots?lotId=SL1")
    txt = N._format_alert(t, "печать", alert_seq=2, db_id="U-2")
    assert "supplier/lots?lotId" not in txt
    assert "%s/U-2" % _VERCEL in txt


def test_push_search_link_is_labeled_desktop_only():
    t = _t("Cooperation.uz Лоты", "https://new.cooperation.uz/supplier/lots?lotId=SL1")
    txt = N._format_alert(t, "печать", alert_seq=3, db_id="U-3")
    assert "Поиск (с десктопа):" in txt


def test_push_ordinary_source_keeps_platform_first():
    """ETender и прочие живые площадки не задеты: прямая ссылка первой."""
    t = _t("ETender UZEX", "https://etender.uzex.uz/lot/500001")
    txt = N._format_alert(t, "печать", alert_seq=4, db_id="U-4")
    assert txt.index("etender.uzex.uz/lot/500001") < txt.index("U-4")
    assert "Архив:" in txt


# --- дайджест ----------------------------------------------------------------

def test_digest_broken_spa_line_gets_an_archive_link():
    """Раньше у таких строк не было НИКАКОЙ ссылки — лот негде посмотреть."""
    t = _t("Cooperation.uz Лоты", "https://new.cooperation.uz/supplier/lots?lotId=SL9",
           title="Регистр папка")
    txt = N._build_digest_text([t], archive={"x": "U-9"})
    assert "%s/U-9" % _VERCEL in txt
    assert "supplier/lots" not in txt


def test_digest_without_archive_map_keeps_old_behaviour():
    """Совместимость: обычный источник со своей ссылкой — как раньше."""
    t = _t("Тест", "https://example.uz/lot/1")
    assert "https://example.uz/lot/1" in N._build_digest_text([t])


def test_digest_sender_actually_passes_the_map():
    """Пин на подключение: карта, которую никто не передал, полезна ровно как
    её отсутствие — та же ловушка, что с кнопкой без клавиатуры (20.08)."""
    src = io.open(os.path.join(_ROOT, "crawler/core/notifier.py"), encoding="utf-8").read()
    i = src.index("async def _send_digest")
    j = src.index("async def send_alerts", i)
    body = src[i:j]
    assert "_build_digest_text(tenders, archive)" in body
    assert "_lookup_tender_uuid" in body


# --- напоминания о дедлайнах -------------------------------------------------

def test_reminder_for_coop_links_to_archive():
    """Битая ссылка со скриншота Данияра (21:09) пришла именно отсюда."""
    row = {"id": "uuid-7", "source": "Cooperation.uz Лоты",
           "source_url": "https://new.cooperation.uz/supplier/lots?lotId=SL7"}
    assert D._reminder_url(row) == "%s/uuid-7" % _VERCEL


def test_reminder_for_ordinary_source_passes_through():
    row = {"id": "uuid-8", "source": "ETender UZEX",
           "source_url": "https://etender.uzex.uz/lot/500002"}
    assert D._reminder_url(row) == "https://etender.uzex.uz/lot/500002"


def test_reminder_without_uuid_falls_back_to_platform():
    """Хоть какая-то ссылка лучше, чем никакой: без uuid отдаём платформу."""
    row = {"source": "Cooperation.uz Лоты", "source_url": "https://x/lots?lotId=1"}
    assert D._reminder_url(row) == "https://x/lots?lotId=1"


def test_both_reminder_formats_use_the_helper():
    src = io.open(os.path.join(_ROOT, "crawler/core/deadline_tracker.py"), encoding="utf-8").read()
    assert src.count("_reminder_url(") >= 3, "хелпер должен стоять в одиночном И в сводном формате"
    assert 'url = tender.get("source_url")' not in src
    assert 'url = t.get("source_url")' not in src


# --- offer-join --------------------------------------------------------------

def _fetch_src():
    return io.open(os.path.join(_ROOT, "scripts/fetch_cooperation.py"), encoding="utf-8").read()


def test_offer_lookup_searches_by_number_not_by_title():
    """Точный поиск: GetAllOffer(productName=<номер>) возвращает total=1
    (живой зонд 24.08, O1793672). Поиск по словам названия среди 419k оферт
    давал цену у 2% строк."""
    src = _fetch_src()
    i = src.index("def _fetch_offer_detail")
    body = src[i:src.index("def _enrich_lot_row")]
    assert "'productName': offer_number" in body, "точный поиск по номеру пропал"
    assert "'productName': q" in body, "фолбэк на поиск по названию удалён — а он страховка"


def test_pick_offer_is_exact_and_safe():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "coop_pure", os.path.join(_ROOT, "scripts/fetch_cooperation.py"))
    # модуль тянет прод-окружение при импорте — берём функции разбором AST
    import ast
    tree = ast.parse(_fetch_src())
    ns = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("_pick_offer", "_offer_payload"):
            exec(compile(ast.Module([node], []), "<coop>", "exec"), ns)
    pick, payload = ns["_pick_offer"], ns["_offer_payload"]
    items = [{"offerNumber": "O1"}, {"offerNumber": "O2", "unitPrice": 5}]
    assert pick(items, "O2")["unitPrice"] == 5
    assert pick(items, "O9") is None
    assert pick(None, "O1") is None
    # ведущий '|' в photos — живой пример O2419735
    out = payload({"offerNumber": "O2", "unitPrice": 5,
                   "photos": "|contractfiles/a.webp", "company": {}})
    assert out["photo"].endswith("contractfiles/a.webp")
    assert "//ocelot" not in out["photo"].replace("https://", "")


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
