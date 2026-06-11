# Аудит deep-links — кластер industry2 (uz-kor, saneg, mobiuz)
Дата: 2026-06-11. Режим: read-only.

## Конфиги (sources.yaml)
- uz-kor (строка 1845): name="Уз-Кор Газ Кимё", adapter=html, enabled=true, url=https://www.uz-kor.com/index.php/ru/tendery, container="table.category tbody tr", title="td.list-title a", link="td.list-title a@href", source_url_template="https://www.uz-kor.com{link}"
- mobiuz (строка 1893): name="Mobiuz", adapter=html, enabled=true, url=https://company.mobi.uz/ru/purchase/, container="div.news-item", title="h2.news-line__name a", link="h2.news-line__name a@href", source_url_template="https://company.mobi.uz{link}"
- saneg (строка 2446): name="Saneg Тендеры", adapter=html, enabled=true, url=https://www.saneg.com/tenders, container="div.news-one.news-list-one", title="h4.news-one-title a", link="h4.news-one-title a@href", source_url_template="{link}" (абсолютный href ожидается)

## БД (Supabase, tenders) — свежесть
Все 3 источника живые, collected_at = 2026-06-11 18:00 (последний прогон).
- Saneg Тендеры: ids 1568..1579, пример https://www.saneg.com/tender/1568-provedenie-rabot-po-diagnostirovaniyu-i-remontu-analizatora-mikrosery-xplorer-ns
- Уз-Кор Газ Кимё: ids 5555..5732, пример https://www.uz-kor.com/index.php/ru/tendery/5732-...-akkumulyatornykh-batarej
- Mobiuz: id 2026, url https://company.mobi.uz/ru/purchase/2026/102891/ (external_id="2026" подозрителен — похоже на ГОД из URL, не на id лота; в URL два сегмента /2026/102891/)

## НАХОДКА (Mobiuz, критично): external_id = ГОД, не id лота
В БД ВСЕГО 2 строки Mobiuz за всю историю: external_id="2025" (collected 2026-03-03) и external_id="2026" (collected 2026-06-11).
URL-формат /ru/purchase/{ГОД}/{ID}/ — краулер берёт первое число из href => год. Дедуп по external_id схлопывает ВСЕ лоты года в одну запись => почти все тендеры Mobiuz пропускаются (1 запись в год).

## Корень Mobiuz-бага (crawler/adapters/html.py ~строка 230)
`id_match = re.search(r"(\d+)", link)` — берёт ПЕРВОЕ число из href. Mobiuz href = `/ru/purchase/2026/102891/` → ext_id="2026" (год). Все лоты одного года = один external_id => дедуп съедает все кроме первого. Подтверждено БД: ровно 2 строки (2025, 2026) за всю историю.
Fix: нужен код-фикс (конфигом не лечится) — per-source опция `html_selectors.external_id_regex` (для mobiuz: `(\d+)/?$` → 102891), либо в html.py брать ПОСЛЕДНЮЮ числовую группу сегмента. Глобально менять "первое→последнее число" рискованно для других html-источников — лучше opt-in regex.

## Mobiuz листинг live-проверка (2026-06-11)
HTTP 200, селекторы валидны: 20 контейнеров div.news-item / h2.news-line__name a. Свежие href: /ru/purchase/2026/103197/, /2026/103185/, /2026/103184/ ... — на листинге 20 лотов 2026 года, в БД лишь 1 (id-бага подтверждена: теряется ~95% лотов).

## Mobiuz детальная страница
https://company.mobi.uz/ru/purchase/2026/102891/ → HTTP 200, <title> = полный титул лота, совпадает с title в БД. Deep-link DIRECT_OK. Проблема ТОЛЬКО в external_id (дедуп), не в ссылках.

## uz-kor листинг live-проверка
HTTP 200, селекторы валидны: table.category=1, td.list-title=15, td.list-date=15, 15 href вида /index.php/ru/tendery/5732-...; совпадает со свежими записями БД (5732 есть в обоих). external_id=первое число (5732) — здесь корректно (Joomla id ведёт slug).

## uz-kor детальная + негативный тест
- https://www.uz-kor.com/index.php/ru/tendery/5732-... → HTTP 200, <title> = титул лота (CS-112/26, аккумуляторные батареи), 3 вхождения "CS-112" в теле. DIRECT_OK.
- Негатив: /index.php/ru/tendery/99999-bogus-tender → HTTP 404 (корректно, битый id не маскируется).

## saneg листинг live-проверка
HTTP 200, селекторы валидны: 12 контейнеров div.news-one.news-list-one, 12 h4.news-one-title, 12 span.news-one-date. href абсолютные (https://www.saneg.com/tender/1579-...) — шаблон "{link}" + ветка "link.startswith('http') → напрямую" в html.py отрабатывает. Совпадает с БД (1568..1579).

## saneg детальная + негативный тест
- https://www.saneg.com/tender/1579-gazoporshnevaya-elektrostanciya → HTTP 200, <title>=Газопоршневая электростанция (=title в БД). DIRECT_OK.
- Негатив: /tender/999999-bogus → HTTP 404 (корректно).

## ИТОГ
| source | вердикт | детали |
|---|---|---|
| uz-kor (Уз-Кор Газ Кимё) | DIRECT_OK | листинг и селекторы валидны (15 лотов), deep-link 200 с титулом лота, негатив 404. Свежесть БД: 2026-06-11. Fix не нужен. |
| saneg (Saneg Тендеры) | DIRECT_OK | листинг и селекторы валидны (12 лотов), абсолютные href обрабатываются корректно, deep-link 200 с титулом, негатив 404. Свежесть БД: 2026-06-11. Fix не нужен. |
| mobiuz (Mobiuz) | DIRECT_OK по ссылке, НО КРИТИЧНЫЙ id-баг | deep-link 200 с титулом, негатив 404, селекторы валидны (20 лотов на листинге). НО external_id = ГОД из href /ru/purchase/{YYYY}/{ID}/ (html.py берёт первое число) → дедуп схлопывает все лоты года: в БД 2 строки за всю историю (2025, 2026), потеряно ~95% лотов. |

### Fix для mobiuz (код, не конфиг)
В crawler/adapters/html.py (блок "External ID: from link", ~стр. 230) добавить opt-in per-source regex:
1) В схему html_selectors добавить поле `external_id_regex` (Optional[str]).
2) В html.py: `if selectors.external_id_regex: m = re.search(selectors.external_id_regex, link); ext_id = m.group(1) if m else <fallback к текущей логике>`.
3) В sources.yaml для mobiuz: `external_id_regex: "(\d+)/?$"` → из /ru/purchase/2026/102891/ даст 102891 (проверено на живых href: 103197, 103185, 102891 — все уникальны).
Глобальную замену "первое число → последнее" НЕ делать: сломает источники где id ведёт slug (uz-kor 5732-..., saneg 1579-...).
Примечание: после фикса старые записи external_id=2025/2026 останутся; новые лоты пойдут с настоящими id (конфликтов нет — id лотов 6-значные).
