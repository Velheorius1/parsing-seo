"""Токен Cooperation больше не сторожится (21.10.2026).

ЧТО БЫЛО. healthcheck проверял срок токена `cooperation`, который 6 раз в сутки
добывал /opt/eimzo/coop_login.py — входом E-IMZO Winch через резидентный прокси
с verify=False. Аудит 21.10: токен не читает ни один источник и скрипт (нет
`auth_platform: cooperation`, нет `get_token("cooperation")`). Вход снят из cron
по решению Данияра; без этой правки через сутки пришёл бы FAIL «EXPIRED» —
ложная тревога о том, что ничему не нужно.

Run: .venv/bin/python3 -m pytest crawler/tests/test_coop_token_check_removed.py -q
"""
from types import SimpleNamespace
from unittest.mock import patch

from crawler.tests._stubs import install_settings_stub

install_settings_stub()

import crawler.scripts.healthcheck as H  # noqa: E402


def test_cooperation_token_is_not_checked():
    hc = H.HealthCheck()
    with patch("crawler.auth_eimzo.PLATFORMS", {"ebirja": {}}), \
            patch("crawler.auth.session_store.session_store", SimpleNamespace(_read=lambda _p: None)):
        hc.check_tokens()
    comps = [r["component"] for r in hc.results]
    assert "token.cooperation" not in comps, comps
    assert "token.ebirja" in comps, comps  # остальные платформы проверяются по-прежнему
