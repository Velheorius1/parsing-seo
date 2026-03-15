# Platform Gap Analysis: TenderZone vs Our Sources

**Date:** 2026-03-15
**Source:** TenderZone SBIS API (TradingPlatform.GetList, country=UZ)

## Summary

| Metric | Count |
|--------|-------|
| TZone UZ platforms | 21 |
| Matched to our sources | 12 |
| Missing (gap) | 9 |

## Matched Platforms

| TZone Platform | Our Source ID | Tenders |
|---------------|-------------|--------|
| Узбекская республиканская товарно-сырьевая биржа | `etender` | 2,045,541 |
| Электронный кооперационный портал | `cooperation-plans` | 83,813 |
| Министерство Строительства и Жилищно-Коммунального Хозяйства Республики Узбекистан | `minstroy-tenders` | 54,353 |
| ООО «ХТ ХАРИД ТЕХНОЛОГИЯЛАРИ» | `xt-xarid` | 30,081 |
| Uz-Kor Gas Chemical | `uz-kor` | 594 |
| Закупки силовых ведомств Узбекистана | `exarid-uzex` | 501 |
| TOSHKENT METALLURGIYA ZAVODI | `tashkent-steel` | 469 |
| Mobiuz | `mobiuz` | 352 |
| Хамкорбанк закупки | `hamkorbank` | 253 |
| Asian Development Bank | `adb-uz` | 200 |
| KAPITALBANK | `kapitalbank` | 133 |
| Ucell | `ucell` | 125 |

## Missing Platforms (Gap) — Resolution

| # | TZone Platform | URL | Tenders | Status | Source ID |
|---|---------------|-----|---------|--------|-----------|
| 1 | TendersOnTime | tendersontime.com | 65,469 | SKIP (aggregator, paid) | — |
| 2 | TENDERWEEK | tenderweek.com | 5,079 | ADDED (html) | `tenderweek` |
| 3 | UzAirports | uzairports.com/tender | 297 | ADDED (html) | `uzairports` |
| 4 | Saneg | saneg.com/tenders | 203 | ADDED (html) | `saneg` |
| 5 | Beeline UZ | beeline.uz | 137 | ADDED (spa, disabled) | `beeline-uz` |
| 6 | MinZdrav | ssv.uz | 72 | ADDED (html) | `minzdrav-uz` |
| 7 | Bnect UZ | uz.bnect.pro | 56 | ADDED (spa, disabled) | `bnect-uz` |
| 8 | Uzavtoyul | uzavtoyul.uz | 48 | SKIP (SSL error, 404) | — |
| 9 | TenderGPT | tendergpt.uz | 5 | SKIP (too few tenders) | — |

## All TZone UZ Platforms (raw)

| ID | Name | Brief | URL | Tenders |
|----|------|-------|-----|--------|
| 422 | Узбекская республиканская товарно-сырьевая биржа | Узбекская республиканская товарно-сырьевая биржа | http://uzex.uz | 2,045,541 |
| 5611 | Электронный кооперационный портал | Электронный кооперационный портал | https://stat-new.cooperation.uz/all-deals | 83,813 |
| 5579 | TendersOnTime | TendersOnTime | https://www.tendersontime.com/uzbekistan-tenders/ | 65,469 |
| 59 | Министерство Строительства и Жилищно-Коммунального Хозяйства Республики Узбекистан | Министерство Строительства и Жилищно-Коммунального Хозяйства Республики Узбекистан | https://tender.mc.uz/ | 54,353 |
| 368 | ООО «ХТ ХАРИД ТЕХНОЛОГИЯЛАРИ» | XT-XARID | https://xt-xarid.uz/ | 30,081 |
| 5559 | TENDERWEEK | Tenderweek | http://tenderweek.com | 5,079 |
| 5587 | Uz-Kor Gas Chemical | Uz-Kor Gas Chemical | https://www.uz-kor.com | 594 |
| 5617 | Закупки силовых ведомств Узбекистана | Закупки силовых ведомств Узбекистана | http://exarid.uzex.uz | 501 |
| 5558 | TOSHKENT METALLURGIYA ZAVODI | Toshkent Metallurgiya Zavodi | http://tashkentsteel.uz/contests/ | 469 |
| 5612 | Mobiuz | Mobiuz | https://company.mobi.uz/ru/purchase/ | 352 |
| 5591 | UzAirports | Uzbekistan Airports | https://uzairports.com/tender | 297 |
| 5555 | Хамкорбанк закупки | Hamkorbank | https://hamkorbank.uz/press-center/tenders/ | 253 |
| 5549 | Saneg тендеры | Saneg | https://www.saneg.com/ | 203 |
| 4889 | Asian Development Bank | Asian Development Bank | https://www.adb.org/ | 200 |
| 5605 | Beeline UZ | Beeline UZ | https://beeline.uz/ru/about/tenderi | 137 |
| 5560 | KAPITALBANK | Kapitalbank | https://www.kapitalbank.uz | 133 |
| 5619 | Ucell | Ucell | https://tender.ucell.uz/ | 125 |
| 4879 | Министерство здравоохранения Республики Узбекистан | Министерство здравоохранения Республики Узбекистан | https://ssv.uz/ru/ | 72 |
| 5707 | Bnect UZ | Bnect UZ | https://uz.bnect.pro/procurement | 56 |
| 5561 | Автомобильный комитет Республики Узбекистан | Комитет по автомобильным дорогам Узбекистана | https://www.uzavtoyul.uz/ru/ | 48 |
| 5711 | ЭТП «TenderGPT» | TenderGPT | https://tendergpt.uz/ | 5 |
