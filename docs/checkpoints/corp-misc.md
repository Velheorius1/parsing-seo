# Аудит deep-links — кластер misc (uzairports, tenderweek, gov-eco-tenders)
Дата: 2026-06-11

## 1. Конфиги (sources.yaml)

### uzairports (name: "Uzbekistan Airports")
- adapter: html, enabled: true, url: https://uzairports.com/tender
- container: `div.card.card--details.card--traffic-stats`, title: `p.title`, deadline: `div.rich-text-content p`, link: ПУСТО
- source_url_template: СТАТИЧЕСКИЙ "https://uzairports.com/tender" — все лоты ссылаются на общий листинг (deep-link отсутствует by design)

### tenderweek (name: "TenderWeek.com")
- adapter: html, enabled: true, url: https://tenderweek.com/
- container: `div.short-item`, title: `h3 a`, deadline: `div.dates`, link: `h3 a@href`
- source_url_template: "https://tenderweek.com{link}"
- NB: есть второй конфиг tg-tenderweek (telegram @tenderweekcom) — disabled 2026-04-28, low-signal

### gov-eco-tenders (name: "Минэкономики (тендеры)")
- adapter: html, enabled: true, url: https://gov.uz/ru/eco/pages/tenderlar
- container: `table tbody tr`, title: `td`, link: `td a@href`
- source_url_template: "{link}" (абсолютный/относительный href как есть)

## 2. БД (tenders, 2026-06-11)

| source | cnt | last_collected | состояние |
|---|---|---|---|
| TenderWeek.com | 356 | 2026-06-11 18:00 | живой, deep-links вида https://tenderweek.com/tender-35915 |
| Uzbekistan Airports | 57 | 2026-06-11 18:00 | живой, тайтлы валидные (лоты "241/26...", "242/26..."), НО source_url = "" (ПУСТО — даже статический шаблон не применился) |
| Минэкономики (тендеры) | 54 | 2026-06-11 18:00 | собирает МУСОР: тайтлы "E-mail:", "Телефон: (71)207-07-70(#9991)" — селектор table tbody tr цепляет контактную таблицу, source_url = "" |

Гипотезы:
- uzairports: link selector пуст → {link}="" → возможно адаптер кладёт "" вместо рендера статического шаблона без плейсхолдеров? Проверить адаптер.
- gov-eco: страница gov.uz/ru/eco/pages/tenderlar изменилась или SPA — таблица тендеров не парсится, парсятся контакты.

## 3. Корень пустых source_url — html.py:204-230
Код: `source_url = ""` и заполняется ТОЛЬКО внутри `if link:`. Статический source_url_template БЕЗ плейсхолдеров (кейс uzairports: "https://uzairports.com/tender") никогда не применяется, т.к. link-селектор пуст → link="" → ветка не выполняется → в БД source_url="".
То же у gov-eco: селектор `td a@href` не находит <a> в строках → link="" → source_url="".
Fix (адаптер, 1 строка): после блока `if link:` добавить
```python
elif cfg.field_map.source_url_template and "{" not in cfg.field_map.source_url_template:
    source_url = cfg.field_map.source_url_template  # статический шаблон без плейсхолдеров
else:
    source_url = page_url  # fallback на листинг
```

## 4. uzairports — живая проверка (2026-06-11)
- https://uzairports.com/tender → HTTP 200, 160KB, 10 контейнеров `card--traffic-stats`, тайтлы валидные ("250/26 ... URGANCH XALQARO AEROPORTI ... baholash xizmatini xarid qilish")
- В карточках НЕТ ссылок на detail-страницы — только `<a href="#aem-ajaxpopup">` (модалка). Deep-link на лот физически невозможен.
- Конфиг-замысел (статический template = листинг) верный, но адаптер его игнорирует (см. п.3) → в БД source_url="".
- Вердикт: DEAD_LINK (пустой URL в алёртах). Fix: адаптерный fallback из п.3 → все лоты получат https://uzairports.com/tender (листинг, лучшее возможное).

## 5. tenderweek — живая проверка (2026-06-11)
- Листинг https://tenderweek.com/ → 200, 10 контейнеров `div.short-item`, href="/tender-35793" и т.д. — селекторы и шаблон работают, URL строится верно.
- Deep-link https://tenderweek.com/tender-35915 → **302 → /login?back=/tender-35915** (страница "Авторизация"). Аноним тайтл лота НЕ видит.
- Негативный тест: /tender-9999999 → 404 (битые id отличимы — ссылка с валидным id "живая", просто за логином).
- Вердикт: AUTH_REQUIRED. Ссылка правильная: после логина ?back= вернёт на лот. Fix конфига не нужен. (Опция: в алёрте помечать "требуется аккаунт tenderweek".)

## 6. gov-eco-tenders — живая проверка + фикс (2026-06-11)
- https://gov.uz/ru/eco/pages/tenderlar → 200, 301KB, статический HTML (не SPA). 25 tbody, 47 tr.
- Структура: одно объявление = tbody из 2-4 tr: tr1="ОБЬЯВЛЕНИЕ !", tr2=реальный тайтл, tr3+=контакты (E-mail/Телефон) + Google Docs ТЗ.
- Текущий container `table tbody tr` нарезает объявление на 3 записи → мусор в БД ("E-mail:", "Телефон:...", "ОБЬЯВЛЕНИЕ !" x8). Ссылок в тайтл-строках нет → source_url="".
- Detail-страниц на лоты НЕ существует (только Google Docs ТЗ в соседней строке).
- **ПРОВЕРЕННЫЙ ФИКС** (прогнан на живом HTML, 13/13 чистых тайтлов, 0 мусора):
  - container: `table tbody tr:nth-child(2)`
  - title: `td`
  - link: `""`
  - source_url_template: `"https://gov.uz/ru/eco/pages/tenderlar"` (статический; требует адаптерного фикса из п.3, иначе останется "")
  - Потеря: одиночные tbody с 1 tr (1 шт: "ОБЪЯВЛЕНИЕ О НАЧАЛЕ ОТБОРА АУДИТОРСКОЙ ФИРМЫ") — нерелевантно для полиграфии, приемлемо.

## ИТОГ

| source | verdict | suть | fix |
|---|---|---|---|
| uzairports | DEAD_LINK | Краулинг живой, тайтлы валидные, но source_url="" в БД: link-селектор пуст, а html.py применяет source_url_template ТОЛЬКО при непустом link. Сайт не имеет detail-страниц (карточки = модалки #aem-ajaxpopup) | Адаптерный фикс html.py (~строка 204): если link пуст и template без плейсхолдеров → source_url = template; иначе fallback page_url. Конфиг менять не надо |
| tenderweek | AUTH_REQUIRED | Deep-link корректен (https://tenderweek.com/tender-35915 → 302 /login?back=/tender-35915, негатив-тест /tender-9999999=404). Лот за логином, но back= вернёт на него после авторизации | Не нужен |
| gov-eco-tenders | WRONG_PAGE | Селектор table tbody tr цепляет служебные строки (E-mail/Телефон/ОБЬЯВЛЕНИЕ!) как отдельные "тендеры", source_url="" | container: `table tbody tr:nth-child(2)`, title: `td`, source_url_template: статический листинг + адаптерный фикс из п.3. Проверено на живом HTML: 13/13 чистых |

Общий системный баг: html.py не поддерживает статический source_url_template без плейсхолдеров (затрагивает все link-less html-источники, не только этот кластер).
