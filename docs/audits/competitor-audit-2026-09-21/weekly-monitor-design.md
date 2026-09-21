# Weekly competitor-award monitor — безопасный контур

Реализован **offline-only all-exchange** контур. Он не добавлен в cron и не
вызывает Telegram: выпуск в production требует отдельного согласования.

```text
9 public source adapters (30-day overlap)
  → exact INN where publicly disclosed
  → Ebirja Shop name-candidate queue → ≤25 detail cards
  → complete source snapshots only
  → local delta + content-hash revision report
  → manual review / separate delivery decision
```

## Инварианты

* Порог: итог договора строго `>20 000 000 UZS`; ровно 20 млн исключается.
* В state допускаются только детали с точным ИНН и подтверждённой UZS-валютой.
* Неполный архив или недогруженная хотя бы одна candidate-card дают отчёт, но
  **не сдвигают state**. Это предотвращает ложное «новое/исчезло» после сбоя.
* Для complete exact-INN источника state хранит ключ и content hash. Изменение
  суммы, статуса, победителя или формулировки выходит как `changed_award`, а
  не как новая победа.
* Name-candidate — только очередь на detail. После detail компания связывается
  только по `producer.tin`; CENTRIS и CENTRIS-PRINT не смешиваются.
* Detail-запросы ограничены `--max-details` (default 25), поэтому недельный
  запуск не превращается в массовую проверку полного каталога.

## Проверочные прогоны 21.09.2026

Complete snapshot дал две новые верифицированные записи и `state_advanced=false`:

* `ebirja-shop:205353003:XD26000945` — KOLORPAK, 325 190 000 UZS;
* `ebirja-shop:308717019:XD26000929` — отдельный CENTRIS-PRINT, 223 020 000 UZS.

Короткий live-run охватывает все девять строк паспорта: ETender Deals,
UZEX Direct, Ebirja Shop/Auction/Tender/Selection, Cooperation, XT-Xarid и
Hayotbirja. Direct прошёл до временной границы; Deals и Ebirja Shop честно
отмечены incomplete при page cap. Auction/Tender/Selection не раскрывают ИНН
победителя, Cooperation не раскрывает валюту, XT/Hayot не раскрывают
победителя. Это не нулевые результаты.

Raw snapshot и preview намеренно не добавлены в Git: они являются evidence
конкретного запуска. Скрипты `run_all_exchange_competitor_monitor.py`,
`collect_uzex_award_api.py`, `enrich_ebirja_shop_candidates.py` и
`monitor_competitor_awards.py` покрыты unit-тестами и не импортируют settings,
Supabase или Telegram.

## Что нужно до боевого включения

1. Выбрать устойчивое production-хранилище state и retention raw receipts.
2. Добавить расписание с 30-дневным overlap и ограничением page cap;
   page-cap/HTTP/schema error должен быть видимым как failed run, не как zero.
3. Решить delivery policy: сначала отчёт Данияру/менеджеру, а не прямой alert
   каждого результата; договор уже является ретроспективным сигналом.
4. Отдельно протестировать добавление Ebirja detail в активный lead-crawler на
   frozen corpus и сравнить alert noise. Договорный монитор не заменяет поиск
   открытых тендеров.
