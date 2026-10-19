"""Прямая закупка — это заключённый договор, и алерт обязан так и говорить (19.10.2026).

ЧТО БЫЛО. Алерт #10006 ушёл «[ТЕНДЕР]» с «Дедлайн: 2026-10-19» и «📝 Подача
КП»: SQB заключил прямой договор №2-В/645 с единственным поставщиком «Давлат
Белгиси» (бланки с водяными знаками, 187,6 млн). Поле срока у источника — дата
договора. Ссылка «Открыть на площадке» вела в э-магазин, на удалённую оферту
болгарки 2023 года: id прямой закупки подставлялся в /home/shop/detail/.

Решение Данияра 19.10: не глушить (кто у кого что купил — разведка), а слать с
пометкой. Здесь держится: шапка «📄 договор уже заключён», «Дата договора»
вместо «Дедлайн», нет «Подача КП», первой идёт ссылка на страницу договора
/home/purchase/direct/detail/{id}; в дайджесте — «📄 ДОГОВОР». Остальные
источники не задеты.

Run: .venv/bin/python3 -m pytest crawler/tests/test_direct_contract_alert.py -q
"""
import os

import yaml

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

from crawler.core import notifier as N  # noqa: E402
from crawler.core.models import RawTender  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
URL = "https://new-xarid.uzex.uz/home/purchase/direct/detail/4447704"


def _direct():
    return RawTender(
        id="direct-261200864447704", external_id="261200864447704",
        title="Услуги печатные и услуги по копированию звуко- и видеозаписей, а также программных средств",
        organization="O'Z SQB ATB", source="Xarid Прямые закупки", source_url=URL,
        price=187600000.0, deadline="2026-10-19T00:00:00",
        extra_info={"Поставщик": "Давлат Белгиси МЧЖ (ИНН 306612737)", "ИНН заказчика": "200833707"})


def test_template_points_to_the_contract_page():
    raw = yaml.safe_load(open(os.path.join(_REPO, "crawler", "config", "sources.yaml")))["sources"]
    tpl = [s for s in raw if s["id"] == "xarid-direct"][0]["field_map"]["source_url_template"]
    assert tpl.format(id=4447704) == URL, tpl


def test_alert_says_contract_not_tender():
    text = N._format_alert(_direct(), "печат", alert_seq=10006, db_id="a0d65c23")
    assert "📄 [ДОГОВОР УЖЕ ЗАКЛЮЧЁН — подать нельзя]" in text, text
    assert "[ТЕНДЕР]" not in text and "Подача КП" not in text, text
    assert "Дата договора: 2026-10-19T00:00:00" in text and "Дедлайн" not in text, text
    assert "Поставщик: Давлат Белгиси" in text, text
    lines = [l for l in text.splitlines() if l.startswith("http")]
    assert lines and lines[0] == URL, lines


def test_digest_line_is_marked():
    assert "📄 ДОГОВОР" in N._build_digest_text([_direct()])


def test_other_sources_are_untouched():
    t = RawTender(id="etender-1", external_id="1", title="Печать календарей",
                  organization="Хокимият", source="ETender UZEX", deadline="2026-10-25")
    text = N._format_alert(t, "печат")
    assert "Дедлайн: 2026-10-25" in text and "📄" not in text, text
    assert "📄" not in N._build_digest_text([t])
