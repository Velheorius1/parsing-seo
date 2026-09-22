# C4: plan — exact Ebirja Shop winner identity

1. Добавить чистую функцию exact-INN фильтра после detail-enrichment.
2. Вынести неверные detail ИНН из `awards`, сохранив audit-счётчик.
3. Закрепить тестом: похожее имя + чужой ИНН исключается, реальный реестровый
   ИНН проходит; completeness detail-fetch не меняется.
4. Выпустить отдельно и проверить на VPS offline-набором.
