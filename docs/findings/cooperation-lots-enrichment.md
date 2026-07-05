# Cooperation Лоты organization enrichment — blocked

**Date:** 2026-04-20
**Status:** BLOCKED — requires authorized API access
**Tried:** `LotRequest/GetLotInfo` — not reachable publicly

## Проблема
27,099 записей в `tenders` с `source='Cooperation.uz Лоты'` имеют `organization=None`.
`GetLotsInTrade` возвращает ровно 12 полей и company среди них нет:
```json
{
  "statusId", "beginDate", "endDate", "lotTnved", "lotEnkt",
  "measureName", "productName", "lotNumber", "offerNumber",
  "quantity", "isCertificate", "minPart", "maxPart"
}
```

## Исследование endpoints

Пробовал:
- `new.cooperation.uz/ocelot/api-shop/LotRequest/GetLotInfo?lotNumber=...` → 404
- `new.cooperation.uz/ocelot/api-shop/LotRequest/GetLot` → 404
- `new.cooperation.uz/ocelot/api-shop/OfferRequest/*` → 404
- `new.cooperation.uz/ocelot/api-direct/Offer/*` → 404
- `cabinet.cooperation.uz/api/offer/*` → 404 (приватный)
- `cabinet.cooperation.uz/api/bid-flow/lot/*` → 404
- Vue-бандл `index.*.js` не содержит `ocelot/*/LotRequest/*` паттернов — эти
  endpoints вызываются только из authenticated контекста.

## Playwright
Navigate to `https://new.cooperation.uz/supplier/lots` — 0 API вызовов
(редирект на логин, если не авторизован).

## Что работает публично
`AuctionPublicLots` (`cabinet.cooperation.uz/api/auction/public/lots`) — уже
возвращает `companyName` и используется в `fetch_and_transform_auction_lots()`.
Это OTHER источник — `Cooperation.uz Аукционы`, не `Лоты`.

## Следующие шаги
1. Регистрация + E-IMZO авторизация на cooperation.uz (уже есть план Данияра)
2. После авторизации — захватить через Playwright реальный endpoint в DevTools
3. Добавить `GetLotInfo` как authorized endpoint в crawler (batch enrichment
   с rate_limit 3 QPS, backfill через `scripts/`)
4. Alternative: deprecate `Cooperation.uz Лоты` источник если overlap с
   `Cooperation.uz Аукционы` (которые уже имеют organization)

## UPDATE 2026-07-05 — проверено С живым E-IMZO токеном: авторизация НЕ разблокирует детали

Bearer-токен (auth.cooperation.uz, org=89986) получен и жив. Проверено через UZ-прокси:
- `LotRequest/GetLotInfo|GetLotDetail|GetLotInTrade|GetLotByNumber`, `Lot/Get`,
  `cabinet/api/shop/lot/info`, `cabinet/api/bid-flow/lot/{id}` → **404 и с токеном**.
- `GetLotsInTrade?lotNumber=SL…` → 200, но **фильтр игнорируется** (та же 1-я страница),
  и **с токеном и без — идентичные 12 полей**.

**Вывод (финальный): детального API лота НЕ СУЩЕСТВУЕТ.** SPA рендерит карточку из тех
же 12 полей листинга (совпадает с апрельским сканом Vue-бандла — детальных роутов нет).
Заказчик лота публично не раскрывается нигде. Текущие алерты уже показывают ВСЁ, что
платформа отдаёт: Кол-во/Партия/Сертификат/ТНВЭД + Цена/Фото через offer-join (015).

**Единственная неисследованная дверь:** cabinet bid-flow эндпоинты, которые SPA зовёт
при РЕАЛЬНОМ УЧАСТИИ в лоте (подача ставки) — там заказчик обязан раскрыться. Требует
authenticated-SPA capture (Playwright + сессия) отдельной сессией — или один ручной
проход Данияра по подаче ставки с открытым DevTools/HAR-экспортом.
