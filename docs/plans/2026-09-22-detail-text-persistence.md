# C2: plan — persistence detail/specification

1. Добавить opt-in флаг к конфигурации источника и эпемерный флаг к `RawTender`.
2. Сохранять detail-текст API в скрытом поле `extra_info`.
3. Расширить существующее чтение ключей upsert: для opted-in лотов читать
   только нужный `extra_info`, восстановить detail до формирования строк.
4. Скрыть internal metadata в formatter и закрепить поведение изолированными
   тестами: generic detail, prequalification lots, no-op без opt-in.
5. Запустить тесты локально, затем на VPS после отдельного merge/deploy.
