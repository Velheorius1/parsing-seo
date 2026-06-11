# Аудит deep-links — кластер corp-major (agmk, railway, beeline-uz)
Дата: 2026-06-11

## 1. Конфиги (sources.yaml)

### agmk (АГМК, строка ~2090)
- adapter: html, enabled: true, name: "АГМК (Алмалык ГМК)"
- url: https://agmk.uz/ru/tenders
- selectors: container=".news-inner__item", title="p", deadline="", link="" (ПУСТОЙ)
- source_url_template: "https://agmk.uz/ru/tenders" — СТАТИЧЕСКИЙ листинг, deep-link отсутствует by design

### railway (строка ~1941)
- adapter: html, enabled: true, name: "Узбекистон темир йуллари (ЖД)"
- url: https://railway.uz/ru/informatsionnaya_sluzhba/tendery/vnutregosudarstvenniye/
- selectors: container="li.item", title="h4.news-item__title a", deadline="time.news-info__time", link="a.full-link@href"
- source_url_template: "https://railway.uz{link}" — настоящий deep-link

### beeline-uz (строка ~2471)
- adapter: api, enabled: true, name: "Beeline UZ Тендеры"
- url: https://beeline.uz/msapi/web/page/tenderi?locale=ru, response_path: constructor.0.fields.rows
- external_id: hash:title (API не отдаёт id/slug — только {title, content, opened})
- source_url_template: "https://beeline.uz/ru/about/tenderi" — СТАТИЧЕСКАЯ страница тендеров, deep-link невозможен by design

## 2. БД (tenders, последние 2 строки на источник, 2026-06-11)

### АГМК (Алмалык ГМК) — свежий (2026-06-11 10:00)
- 94376db28d26 | "Наименование работы (услуг): Разработка ТЭО..." | source_url = "" (ПУСТОЙ!)
- 9779439f617a | "Разработка ТЭО проекта «Освоение участка «Междуречь..." | source_url = "" (ПУСТОЙ!)
- Конфиг задаёт статический template, но в БД пусто — template не применяется (вероятно потому что link selector пустой).

### Узбекистон темир йуллари (ЖД) — МОЛЧИТ с 2026-06-05, данные = МУСОР
- 998712379668 | title="+998 71 237 96 68" | source_url="https://railway.uztel:+998712379668"
- 998712379998 | title="+998 71 237 99 98" | source_url="https://railway.uztel:+998712379998"
- Краулер парсит ТЕЛЕФОНЫ как тендеры: селектор li.item цепляет контактные блоки, a.full-link@href отдаёт tel:-ссылки, шаблон конкатенирует "https://railway.uz"+"tel:+998..." = невалидный URL.

### Beeline UZ Тендеры — свежий (2026-06-11 10:00)
- 348319966a410c2f | "Конкурс по выбору Поставщика АГНКС по заправке СПГ..." | source_url=https://beeline.uz/ru/about/tenderi
- 72ad0b6420d31cc1 | "конкурс... энергетического аудита..." | source_url=https://beeline.uz/ru/about/tenderi

## 3. Живые проверки

### AGMK — листинг жив, deep-link ДОСТУПЕН, но конфиг его не берёт
- https://agmk.uz/ru/tenders → HTTP 200, 12 итемов .news-inner__item
- Каждый контейнер САМ является <a href="https://agmk.uz/ru/tenders/<slug>" class="news-inner__item"> — абсолютный deep-link есть в разметке!
- Проверка детали: https://agmk.uz/ru/tenders/objavlenie-na-poluchenie-predvaritelnyh-tkp-tes → HTTP 200, содержит title лота ("модернизации ТЭЦ АО «Алмалыкский гмк»...")
- Негативный тест: /ru/tenders/nonexistent-bogus-id-12345 → HTTP 404 (корректно)
- В БД source_url пуст: link selector = "" → {link} не рендерится → пустая строка. Fix: брать href контейнера.

### AGMK fix — подтверждён против кода адаптера
- crawler/adapters/html.py:288-300: синтаксис "@href" = атрибут САМОГО контейнера (поддерживается).
- html.py:209-215: абсолютный href используется напрямую, шаблон "host{link}" не дублирует хост.
- FIX agmk: link: "@href" (вместо ""), source_url_template: "https://agmk.uz{link}" — абсолютный href пройдёт напрямую.

### RAILWAY — листинг МЁРТВ: HTTP 404
- https://railway.uz/ru/informatsionnaya_sluzhba/tendery/vnutregosudarstvenniye/ → HTTP 404 (114KB chrome-страница 404)
- На 404-странице есть 2 вхождения news-item__title и full-link — это КОНТАКТНЫЕ блоки с tel:-ссылками → краулер 05.06 распарсил телефоны как "тендеры". Объясняет мусор в БД.
- Ищу новый URL листинга.

### RAILWAY — листинг ПЕРЕЕХАЛ, селекторы валидны на новом URL
- Новый листинг: https://railway.uz/ru/proekty/tendery/vnutregosudarstvenniye/ → HTTP 200, 20 шт li.item
  (старый /ru/informatsionnaya_sluzhba/tendery/vnutregosudarstvenniye/ → 404)
- Подразделы: /ru/proekty/tendery/{vnutregosudarstvenniye,mejdunorodniy,public_procurement}/
- СТАРЫЕ селекторы работают на новой странице: li.item=20, news-item__title=22 (20 тендеров + 2 контакт-блока в chrome, контейнер li.item отфильтровывает), full-link=22, news-info__time=22
- Пример: title="ПЕРЕОБЪЯВЛЕНИЕ... страхование D&O...", link=/ru/proekty/tendery/mejdunorodniy/38159/, time=05.06.2026
- Deep-link проверен: https://railway.uz/ru/proekty/tendery/mejdunorodniy/38159/ → HTTP 200, содержит title лота
- Негативный тест /mejdunorodniy/9999999/ → HTTP 200 (SOFT-404, сайт не отдаёт жёсткий 404 на битый id)
- ВАЖНО: в листинге vnutregosudarstvenniye ссылки ведут и в mejdunorodniy — раздел агрегирует. Возможно стоит краулить родительский /ru/proekty/tendery/ + пагинация, но как минимум смена url в конфиге чинит источник.
- FIX railway: url: "https://railway.uz/ru/proekty/tendery/vnutregosudarstvenniye/" (селекторы и source_url_template без изменений)

### BEELINE-UZ — API жив, deep-link невозможен by design, статическая ссылка корректна
- API https://beeline.uz/msapi/web/page/tenderi?locale=ru → HTTP 200, 61 row, схема {title, content, opened} — id/slug ОТСУТСТВУЮТ (deep-link построить не из чего)
- Страница https://beeline.uz/ru/about/tenderi → HTTP 200, 311KB, это Nuxt SPA (79 вхождений nuxt, msapi подтянут клиентом)
- Сырой HTML НЕ содержит title лотов (grep "АГНКС" = 0) — но в браузере страница рендерит те же rows из msapi → пользователь видит лот по ссылке
- Вердикт: ссылка ведёт на единственно возможную правильную страницу. Fix не нужен.

## ИТОГ

| source | verdict | confidence | суть |
|---|---|---|---|
| АГМК (Алмалык ГМК) | DEAD_LINK | high | source_url в БД ПУСТОЙ (link selector=""), хотя листинг отдаёт абсолютный deep-link в href самого контейнера. FIX: `link: "@href"` + template "https://agmk.uz{link}" (абсолютный href пройдёт напрямую, html.py:209). Проверено: деталь 200 + title, bogus slug = 404. |
| Узбекистон темир йуллари (ЖД) | DEAD_LINK | high | Листинг переехал: старый URL = 404, краулер с 05.06 парсит контактные телефоны с 404-страницы ("https://railway.uztel:+998..."). FIX: `url: https://railway.uz/ru/proekty/tendery/vnutregosudarstvenniye/` — старые селекторы валидны на новом URL (20 li.item, deep-link /38159/ проверен, содержит title). Сайт отдаёт soft-404 (200) на битый id. |
| Beeline UZ Тендеры | DIRECT_OK | medium | Deep-link невозможен by design (API без id/slug), статический URL /ru/about/tenderi = каноническая страница тендеров (Nuxt SPA, рендерит те же 61 row из msapi). Title в сыром HTML нет (JS-render), но источник данных подтверждён. Fix не нужен. |

Запросов: agmk.uz=3, railway.uz=5, beeline.uz=2, supabase=1. Мутаций не было.
