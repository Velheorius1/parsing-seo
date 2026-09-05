"""Исход процедуры из публичного API площадки (05.09).

ИЗ ЧЕГО ВЫРОСЛО. Исход алерта известен только по одной площадке из десятка:
etender. По xt-xarid (~698 алертов) он недоступен, и разведка 05.09 объяснила
почему лишь наполовину: победителя публичный API не отдаёт, но `status` и
`last_price` отдаёт. Наша сторона теряла и это — адаптер схлопывал статус
площадки в active/closed, а неизвестные значения молча считал активными.

Здесь закреплено: сырой статус и факт торга сохраняются, схлопывание для
принятия решений не изменилось, неизвестный статус остаётся активным (лишний
алерт дешевле пропущенного), но перестаёт быть невидимым.

Run: python3 -m crawler.tests.test_jsonrpc_outcome_fields   (exit 1 on failure)
"""
import sys
import types

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.adapters.jsonrpc import JsonRpcAdapter  # noqa: E402


def _cfg(**kw):
    fm = types.SimpleNamespace(title="_goods_title", price="start_price",
                               external_id="id", currency="currency",
                               deadline="close_at", organization="company.title",
                               region="company.region.title_ru",
                               source_url_template="https://xt-xarid.uz/procedure/{external_id}/core")
    base = dict(name="XT-Xarid встречные аукционы", id="xt-xarid-reduction",
                url="https://api.xt-xarid.uz/rpc", rpc_ref="ref_reduction_object_public",
                rpc_method="ref", id_prefix="xtx-red", keywords_fields=["meta.good_maps"],
                field_map=fm, item_filter=None, headers={}, timeout=15,
                rate_limit=2.0, pagination=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def _item(**kw):
    base = {"id": 8486772, "status": "publicated", "start_price": 21562661.32,
            "last_price": 21562661.32, "close_at": "2026-09-07T05:11:41Z",
            "currency": "UZS", "good_list": [{"title": "Бумага А4"}],
            "meta": {"good_maps": [{"name": "Бумага офисная А4"}]},
            "company": {"title": "Заказчик", "region": {"title_ru": "Ташкент"}}}
    base.update(kw)
    return base


def _convert(item):
    adapter = JsonRpcAdapter(_cfg())
    t = adapter._convert_item(item)
    assert t is not None, "конвертация вернула None — тест ничего не проверит"
    return t


def test_raw_platform_status_is_kept():
    t = _convert(_item(status="publicated"))
    assert t.extra_info.get("Статус площадки") == "publicated"
    assert t.status == "active", "решающее схлопывание изменилось"


def test_closed_status_is_kept_too():
    t = _convert(_item(status="not_realized"))
    assert t.extra_info.get("Статус площадки") == "not_realized"
    assert t.status == "closed"


def test_unknown_status_stays_active_but_visible():
    """Лишний алерт дешевле пропущенного, но молчать об этом нельзя:
    площадка меняет словарь статусов без предупреждения."""
    t = _convert(_item(status="совершенно_новый"))
    assert t.status == "active"
    assert t.extra_info.get("Статус площадки") == "совершенно_новый"


def test_bidding_is_recorded_only_when_price_moved():
    same = _convert(_item(start_price=100.0, last_price=100.0))
    assert "Цена после торга" not in same.extra_info

    moved = _convert(_item(start_price=100.0, last_price=87.5))
    assert moved.extra_info.get("Цена после торга") == "87.5"


def test_missing_or_broken_prices_do_not_break_conversion():
    for sp, lp in ((None, 5), (5, None), ("мусор", 5), (5, "мусор")):
        t = _convert(_item(start_price=sp, last_price=lp))
        assert "Цена после торга" not in t.extra_info, (sp, lp)


def test_countdown_field_still_works():
    """Пин на соседа: обратные аукционы живут часами, счётчик критичен."""
    t = _convert(_item(remain_time=1800))
    assert "До закрытия" in t.extra_info
    assert t.extra_info.get("Статус площадки") == "publicated"


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    fails = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS", name)
        except Exception as exc:
            print("FAIL", name, "%s: %s" % (type(exc).__name__, exc))
            fails += 1
    print("\n%d/%d passed" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)
