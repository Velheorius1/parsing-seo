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
