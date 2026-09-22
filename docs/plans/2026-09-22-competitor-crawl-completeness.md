# C3: plan — honest all-exchange crawl completion

1. Валидировать payload и даты в collectors UZEX/Ebirja.
2. Прокинуть incomplete-статус через Ebirja name-only и Cooperation currency-only
   ветки рабочего all-exchange runner.
3. Отличить заполненный паспорт от действительно завершённого полного обхода
   в delta layer.
4. Добавить offline-тесты ложных 200-ответов, отсутствующего pageCount,
   incomplete name-only и статуса девяти источников.
5. Выпустить отдельно и проверить production virtualenv без сетевого crawl.
