# Competitor award digest — дизайн

## Цель

Раз в неделю формировать отдельный Telegram-digest о **новых подтверждённых
победах конкурентов**. Digest является ретроспективной конкурентной разведкой:
он не заменяет и не меняет срочные алерты об открытых закупках.

Первый production scope: публичные Ebirja E-shop contracts. UZEX/ETender и
Direct добавляются только после отдельного адаптера и проверки их стабильного
watermark.

## Поток

```text
Ebirja public archive, 7-day window
  -> exact INN / conservative name-candidate queue
  -> максимум 25 public shop detail cards
  -> exact producer.tin + strictly >20m UZS
  -> local state delta
  -> один Telegram digest
```

1. Collector должен пересечь нижнюю границу 7-дневного окна или завершиться
   по надёжному page-count. Page-cap, schema/HTTP error и недогруженная detail
   card означают incomplete run.
2. Detail запрашивается только для кандидатов с exact INN из registry либо
   name-candidate. Name match сам по себе никогда не создаёт событие digest.
3. В digest попадает только запись с `producer.tin`, суммой строго больше 20m
   и подтверждённой UZS-валютой public E-shop card.
4. State продвигается **только после** complete run и успешного Telegram HTTP
   response. При ошибке повторный запуск может прислать digest повторно, но не
   может потерять событие.

## Формат и доставка

* Частота: понедельник 09:00 Asia/Tashkent.
* Получатель: существующий configured digest/chat target; не новый контакт и
  не отдельный token.
* Один digest за запуск: список до 10 записей, затем счётчик остатка.
* Каждая строка: компания + ИНН, сумма, краткая позиция, источник и ссылка.
* При нуле новых записей Telegram не отправляется; completion и counters
  пишутся в локальный structured run receipt.

## State и хранение

* State — versioned JSON в `/opt/parsing-seo/data/competitor-award-monitor/`;
  ключ — `source:winner_inn:contract_number`.
* Raw page receipts/detail hashes — 90 дней, затем ротация отдельной
  обслуживающей задачей. Не хранить credentials или full Telegram responses.
* Начальный bootstrap создаёт baseline без отправки: historical contracts не
  должны внезапно стать «новостями недели».

## Ошибки и наблюдаемость

* Incomplete run: exit non-zero, state не меняется, healthcheck получает
  отдельный сигнал `competitor_award_digest`.
* Telegram non-200: exit non-zero, state не меняется.
* Empty complete run: exit 0, state может сохранить snapshot keys, Telegram не
  вызывается.
* Hard limits: window 7 дней, page cap 50, detail cap 25, HTTP timeout 30 s.

## Не входит в выпуск

* Изменение `ALERT_KEYWORDS`, AI-gate или текущих urgent alerts.
* Автоподача заявок и уведомления о Direct contracts как об открытом спросе.
* Fuzzy-слияние CENTRIS и CENTRIS-PRINT.
* Cooperation/XT/Hayot: у них пока нет достаточного публичного winner evidence.

## Приёмка

1. Unit tests: strict threshold, exact-INN requirement, incomplete run cannot
   advance state, Telegram failure cannot advance state, baseline is silent.
2. Staging dry-run на public API: receipt complete, ≤25 detail requests, digest
   preview соответствует snapshot.
3. Первый production run создаёт silent baseline; следующий искусственный
   fixture run даёт один digest и один state transition.
4. Проверка cron и healthcheck после установки без restart основного crawler.
