"""Contract: active API detail becomes searchable before the alert pipeline."""
import sys
import types

if "crawler.config.settings" not in sys.modules:
    _m = types.ModuleType("crawler.config.settings")
    _m.settings = types.SimpleNamespace(
        residential_proxy_url="", proxy_url="", user_agent="test",
    )
    sys.modules["crawler.config.settings"] = _m

from crawler.adapters.api import ApiAdapter
from crawler.core.models import SourceConfig


def _adapter(source_id, title_field, ext_field):
    payload = {
        "id": source_id, "name": source_id, "adapter": "api", "url": "https://example.test",
        "id_prefix": source_id, "keywords_fields": [title_field, "_detail_text"],
        "field_map": {"title": title_field, "external_id": ext_field},
    }
    # The production image is still Pydantic v1; local development may be v2.
    cfg = (SourceConfig.model_validate(payload) if hasattr(SourceConfig, "model_validate")
           else SourceConfig.parse_obj(payload))
    return ApiAdapter(cfg)


def test_etender_detail_product_is_in_search_text():
    tender = _adapter("etender", "name", "display_no")._convert_item({
        "name": "Закупка полиграфии", "display_no": "123",
        "_detail_text": "Печать газеты, тираж 50 000 экземпляров",
    })
    assert tender is not None
    assert "Печать газеты" in tender.search_text


def test_xarid_detail_position_is_in_search_text():
    tender = _adapter("competition", "category_name", "id")._convert_item({
        "category_name": "Профессиональные услуги", "id": 456,
        "_detail_text": "Изготовление картонных картхолдеров",
    })
    assert tender is not None
    assert "картонных картхолдеров" in tender.search_text


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
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
