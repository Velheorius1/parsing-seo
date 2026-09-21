# Матрица побед конкурентов и покрытия parsing-seo

Период: 13.09.2025—13.09.2026 для UZEX/Xarid cache; Ebirja public archive
доступен только с 19.11.2025; Cooperation public snapshot 22.09.2025—18.09.2026.
Для подтверждённых побед ETender, Direct и Ebirja: итоговая сумма одного
договора/тендера **строго >20 000 000 UZS**. Cooperation исключён из этого
итога: его публичный реестр не отдаёт валюту.

## Подтверждённые победы

| Источник | Exact-INN побед | Сумма UZS | Что доказано о покрытии |
| --- | ---: | ---: | --- |
| ETender UZEX Deals | 26 accepted | 6 168 507 763,52 | Исторический snapshot связан с 14 победами: 7 прошли бы зафиксированный lexical prefilter, 7 — `no_keyword`. AI и своевременная доставка не реконструированы. |
| UZEX/Xarid Direct | 3 published direct contracts | 293 790 000 | Это post-award договоры, не открытые алерты; historical alert coverage неприменим. Все позиции уже имеют keyword-сигналы. |
| Ebirja E-shop | 2 | 548 210 000 | Exact INN подтверждён только после bounded detail-card. Список договоров без detail не содержит позиции; historical crawler snapshot не доказывает их доставку. |
| **Итого exact-INN** | **31** | **7 010 507 763,52** | Не является оценкой всего рынка: включает только наблюдаемые источники и окна. |

### Карта по компаниям

| Конкурент | Подтверждённые победы >20m | Подтверждённая сумма UZS | Основные наблюдаемые позиции |
| --- | ---: | ---: | --- |
| PECHATNIK VOSTOKA | 17 ETender | 2 796 455 657,92 | инструкции, бланкопечать, полиграфия |
| PRINTUZ | 5 (3 ETender, 2 Direct) | 1 809 908 905,60 | журналы, флаеры/брошюры |
| KOLORPAK | 3 (2 ETender, 1 Ebirja) | 1 242 806 000 | печатные книги, картонный футляр |
| CREDO PRINT GROUP | 2 ETender | 713 400 000 | периодическая газета/печатная продукция |
| DIZAYN-PRINT | 2 (1 ETender, 1 Direct) | 160 590 000 | UV-таблички, мнемосхемы Брайля |
| STANDARD POLIGRAF SERVICE | 1 ETender | 64 327 200 | полиграфия |
| CENTRIS-PRINT (отдельная сущность) | 1 Ebirja | 223 020 000 | ручка с печатью/лаком/логотипом |

`CENTRIS-PRINT` (ИНН `308717019`) не объединён с `CENTRIS` (ИНН `307491912`).

## Как читать «попало бы к нам»

| Сценарий | Вердикт |
| --- | --- |
| ETender, 7 из 14 связанных active rows прошли lexical prefilter | **дошли бы до AI**, но не доказанно до Telegram. |
| ETender, 7 из 14 linked rows получили `no_keyword` | **не дошли бы до AI** в зафиксированной версии словаря. Shadow candidates уже выделены отдельно. |
| Остальные 12 ETender accepted wins | **unknown**: нет сопоставимой active historical строки. |
| Direct | Не применимо: контракт опубликован после выбора поставщика. |
| Ebirja | **unknown**: contracts — отдельный ретроспективный маршрут; detail не доказанно входил в historical alert text. |

Не использовать `telegram_message_id` как доказательство своевременного алерта:
он подтверждает HTTP-acceptance Telegram для 6 snapshot rows, относящихся к 4
процедурам; timestamp отправки в историческом снимке отсутствует.

## Площадки, где «0» запрещён

| Площадка | Статус данных о победителе | Корректная формулировка |
| --- | --- | --- |
| Cooperation | 215 строк текущей public выдачи, exact-INN matches 0; currency/независимый годовой watermark отсутствуют | «В доступной выдаче совпадений нет», не «побед нет за год». |
| XT-Xarid / Hayotbirja | Public RPC не раскрывает победителя/ИНН; Hayot зеркалит XT | `unobservable_publicly`, не ноль и не две независимые площадки. |
| Ebirja до 19.11.2025 | Public archive не вернул ранние строки | Исторический интервал недоступен. |

## Приоритеты после аудита

1. Запускать weekly competitor monitor сначала как digest: новый exact-INN договор
   + detail-позиция + сумма; не превращать договоры в срочные lead-алерты.
2. Для Ebirja сохранять detail только для exact-INN/name-candidate очереди; это
   закрывает скрытые позиции без массового обхода каталога.
3. Shadow-тестировать `yoriqnoma`, contextual `blank*`, `jurnal`, `gazeta` и
   `matbaa`; не промотировать их в боевой словарь без precision выборки.
4. Отдельным production-выпуском измерить detail→search_text на frozen corpus и
   только затем подключать cron/state. Этот аудит production не менял.

## Evidence

* [ETender shadow candidates](keyword-shadow-candidates.md)
* [Ebirja positions](ebirja-shop-position-enrichment.md)
* [Direct positions](uzex-direct-position-enrichment.md)
* [Cooperation coverage](cooperation-public-coverage.md)
* [XT/Hayot boundary](xt-hayot-public-result-coverage.md)
* [Weekly monitor design](weekly-monitor-design.md)
