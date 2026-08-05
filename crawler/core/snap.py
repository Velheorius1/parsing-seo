"""Snap a screenshot of our own /tenders/{uuid} page for Telegram alerts.

Used by the notifier when the source platform is a broken SPA (link slips
to homepage / 404). Our SSR'd Next.js page renders title, organization,
price, deadline, period — and the snapshot survives even if the source
platform later removes the lot.

Output: JPEG bytes uploaded to Supabase Storage; URL stored in
tenders.extra_info.screenshot_url.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DETAIL_BASE = "https://parsing-seo.vercel.app/tenders"

# Sources whose deep-link genuinely requires an authenticated session
# (the public route resolves to a blank SPA or a wrong id-space card).
# История верификаций:
#  - 2026-06-08: Hayotbirja отбор/встречные + XT-Xarid встречные открываются
#    публично -> удалены. UZEX Предкв: deep-link исправлен на
#    proposal-request/detail/{id} (публичный) -> в списке не нужен.
#  - 2026-06-11: "Hayotbirja э-магазин" удалён — источник отключён (PR #8),
#    замена "XT-Xarid э-магазин" имеет рабочие публичные /procedure/{id}/core
#    ссылки (НЕ добавлять сюда). Префикс "xt-xarid" заменён точным именем
#    legacy SPA-источника "xt-xarid.uz": префикс-матчинг случайно зацепил бы
#    будущие xt-xarid-* источники, чьи ссылки публичны.
# Keep in sync with web/src/app/tenders/[id]/page.tsx BROKEN_SPA_HOSTS.
BROKEN_SPA_SOURCES = {
    "Xarid Конкурсы",
    "Xarid Прямые закупки",
    "xt-xarid.uz",
}
BROKEN_SPA_PREFIXES = ("Cooperation.uz",)

# Исключения из префикса — источники, у которых deep-link ПРОВЕРЕН и работает.
# Префикс «Cooperation.uz» накрывает площадку целиком, и это правильно по
# умолчанию: у `Лоты` ссылка упирается в auth-wall, у предквалификаций
# резолвится в чужую карточку. Но правило по префиксу не различает маршруты, а
# они у площадки разные — и из-за этого рабочая ссылка на план закупки
# выбрасывалась из алерта, оставляя человеку только поиск по первому слову
# названия. Данияр 04.08: «невозможно перейти по этой ссылке на конкретный лот».
#
# Проверка, по которой источник попадает СЮДА (иначе не добавлять):
#  1. маршрут есть в бандле SPA и публичен — `/plan-schedule/:id`,
#     name=PlanScheduleDetail, `requiresAuth:!1`;
#  2. бэкенд отдаёт карточку без авторизации —
#     `schedule-plan/schedule-plans/for-client/detail/{guid}` → HTTP 200 с полями
#     name/price/products;
#  3. страница РЕНДЕРИТСЯ (headless Chromium через резидентный прокси, а не
#     проверка по HTTP-коду: SPA отдаёт 200 на любой путь). На лоте
#     bd85f440-76ca-48fd-9353-0118bde0692b в тексте страницы есть позиция
#     «Kitob nashr etish xizmati».
# Наш `external_id` для этого источника — это `guid` площадки, то есть ровно
# тот идентификатор, который ждёт маршрут.
#
# 05.08, «Аукционы» — тот же протокол пройден целиком:
#  1. `/auction/:id` — единственный маршрут аукциона в бандле, и листинг
#     `/auction` строит карточкам href ровно такого вида (пример /auction/690);
#  2. `cabinet.cooperation.uz/api/auction/public/lots` отдаёт лот анонимно и
#     несёт ЧИСЛОВОЙ `id` рядом с `lotNumber`;
#  3. рендер: /auction/690, /auction/689, /auction/683 — 3 из 3 остались на
#     своём URL, и на странице есть название лота из API.
# Из-за чего ссылка не работала: маршрут ждёт числовой `id` (690), а мы клали
# `lotNumber` (AL1000716) — разные пространства id, роутер молча выбрасывает
# на главную. Обратное тоже проверено: по lotNumber 3 из 3 ушли на `/`.
#
# ПРОВЕРЕНО И ОТКЛОНЕНО — остаются под правилом, ссылки на карточку нет вообще:
#  - «Лоты»: `/active-trades` не даёт карточкам <a href>, клик открывает
#    модалку без смены URL;
#  - «Оферты»: то же самое на `/e-catalog`;
#  - «Э-магазин лоты»: маршрут `/e-shop/:id` есть, но адресует товар каталога
#    (id ~180898), а не лот в торгах (id ~13744): по нашему id 2 из 3 выброс
#    на `/`, третий отрисовал чужое.
WORKING_SPA_SOURCES = {
    "Cooperation.uz Закупочные планы (filtered)",
    "Cooperation.uz Аукционы",
}


def is_broken_spa(source):
    # type: (str) -> bool
    if not source:
        return False
    if source in WORKING_SPA_SOURCES:
        return False
    if source in BROKEN_SPA_SOURCES:
        return True
    return any(source.startswith(p) for p in BROKEN_SPA_PREFIXES)


async def capture_our_page(uuid, viewport_w=1080, viewport_h=1350):
    # type: (str, int, int) -> Optional[bytes]
    """Open https://parsing-seo.vercel.app/tenders/{uuid} in headless Chromium
    and return a JPEG screenshot of the visible viewport.

    Returns None on any failure — the caller should fall back to text-only
    sendMessage.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("[snap] playwright not installed")
        return None

    if not uuid:
        return None

    url = "{}/{}".format(DETAIL_BASE, uuid)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            ctx = await browser.new_context(
                viewport={"width": viewport_w, "height": viewport_h},
                device_scale_factor=2,
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
                color_scheme="dark",
            )
            page = await ctx.new_page()
            try:
                await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                try:
                    await page.wait_for_selector("h1", timeout=5000)
                except Exception:
                    pass
                await page.wait_for_timeout(1500)
                shot = await page.screenshot(full_page=False, type="jpeg", quality=85)
                logger.info("[snap] captured %s -> %d KB", uuid, len(shot) // 1024)
                return shot
            except Exception as exc:
                logger.warning("[snap] %s page error: %s", uuid, str(exc)[:200])
                try:
                    return await page.screenshot(full_page=False, type="jpeg", quality=80)
                except Exception:
                    return None
        finally:
            await browser.close()


async def snap_and_upload(uuid, source, external_id):
    # type: (str, str, str) -> Optional[str]
    """High-level helper: capture our page + upload to Supabase Storage.
    Returns the public URL or None.
    """
    from crawler.core.storage import upload_screenshot

    shot = await capture_our_page(uuid)
    if not shot:
        return None
    return upload_screenshot(shot, source or "unknown", external_id or uuid, mime="image/jpeg")
