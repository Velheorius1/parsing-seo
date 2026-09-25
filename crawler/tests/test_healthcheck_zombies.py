"""healthcheck --fix не трогает живые браузеры краула (25.09).

Раньше зомби считался любой chromium, а фикс делал `pkill -f chromium`.
Ежедневный прогон в 06:00 стартует в одну минуту с полным краулом и убивал
его браузеры: 25.09 XT-Xarid, Hayotbirja и E-Birja упали с «browser has been
closed» через секунду после «FIXED zombies». Зомби теперь — chromium старше
часа, и убиваются только они.
"""
import os

import crawler.scripts.healthcheck as H
from crawler.scripts.healthcheck import OK, WARN, HealthCheck

_PS = "\n".join([
    "  101    40 /root/.cache/ms-playwright/chromium-1187/chrome-linux/chrome --headless",
    "  102    38 /root/.cache/ms-playwright/chromium-1187/chrome-linux/chrome --type=renderer",
    "  201  7300 /root/.cache/ms-playwright/chromium-1187/chrome-linux/chrome --headless",
    "  301  9000 /usr/bin/python3 -m crawler.main",
])


class _Ps(object):
    stdout = _PS


def _hc(monkeypatch):
    monkeypatch.setattr(H.subprocess, "run", lambda *a, **k: _Ps())
    return HealthCheck()


def test_live_crawl_browsers_are_not_zombies(monkeypatch):
    hc = _hc(monkeypatch)
    assert hc._stale_chromium_pids() == [201]


def test_fix_kills_only_the_old_browser(monkeypatch):
    hc = _hc(monkeypatch)
    killed = []
    monkeypatch.setattr(H.os, "kill", lambda pid, sig: killed.append(pid))
    hc.check_zombie_processes()
    assert hc.results[-1]["status"] == WARN
    hc.auto_fix()
    assert killed == [201]


def test_only_young_browsers_is_ok_and_kills_nothing(monkeypatch):
    young = "\n".join(l for l in _PS.splitlines() if " 7300 " not in l)
    monkeypatch.setattr(H.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": young})())
    killed = []
    monkeypatch.setattr(H.os, "kill", lambda pid, sig: killed.append(pid))
    hc = HealthCheck()
    hc.check_zombie_processes()
    assert hc.results[-1]["status"] == OK
    hc.auto_fix()
    assert killed == []
