# C1–C5 Reliability Fixes Implementation Plan

> **For Codex:** Execute this plan task-by-task with isolated verification and commits.

**Goal:** Close the reproduced C2–C5 integration gaps without expanding alert scope or replacing the existing monitor architecture.

**Architecture:** Four releases preserve existing boundaries. C2 makes database lookup uncertainty explicit and gives fresh detail priority. C5 adds a file-backed durable delivery outbox. C3 validates Ebirja pagination before state can advance. C4 represents unresolved identity as a non-destructive partial source result.

**Tech Stack:** Python 3.9, existing standalone offline test scripts, Supabase JSONB, atomic local JSON files, GitHub PRs, VPS cron.

---

### Task 1: C2 — preserve fresh and legacy tender detail safely

**Files:**

- Modify: `crawler/core/db.py:175-305`
- Modify: `crawler/tests/test_detail_text_persistence.py`

**Step 1:** Add three regressions: fresh `_detail_text` beats stored detail; legacy stored `search_text` is conservatively migrated; a failed existence lookup is neither written nor returned as new.

**Step 2:** Run `PYTHONPATH=. python3 crawler/tests/test_detail_text_persistence.py`; observe failures on current implementation.

**Step 3:** Return known rows plus unknown lookup keys; remove unknown keys before restore/newness/write. Restore old detail only if the incoming tender lacks it; give a non-empty incoming `lots` payload the same priority. Read stored `search_text` for opted-in rows and migrate only the removable residual to `_detail_text`.

**Step 4:** Run detail tests, alert contract tests, prequalification tests and `python3 -m compileall crawler/core/db.py`.

**Step 5:** Commit only C2 code and tests.

### Task 2: C5 — durable delivery and retry

**Files:**

- Modify: `crawler/scripts/monitor_competitor_awards.py:1-320`
- Modify: `scripts/run_competitor_award_weekly.sh:1-21`
- Modify: `crawler/tests/test_competitor_award_monitor.py`

**Step 1:** Add regressions for a transport exception after batch one, retrying pending events absent from the next source snapshot, and no outbox mutation for preview/bootstrap.

**Step 2:** Run `PYTHONPATH=. python3 crawler/tests/test_competitor_award_monitor.py`; observe failures.

**Step 3:** Add an atomically persisted outbox, merge it into delivery candidates, catch sender exceptions, checkpoint each confirmed batch in state and outbox, and wire the stable outbox path in weekly cron. Preserve existing state-file compatibility.

**Step 4:** Run competitor monitor tests, crawl-completeness tests, `python3 -m compileall crawler/scripts`, and the original C5 probe.

**Step 5:** Commit only C5 code, cron wiring and tests.

### Task 3: C3 — reject contradictory empty pagination

**Files:**

- Modify: `crawler/scripts/collect_ebirja_contract_api.py:65-120`
- Modify: `crawler/tests/test_competitor_crawl_completeness.py`

**Step 1:** Add a failing response with empty data, `pageCount=10`, `totalCount=100`; assert incomplete and preserved source baseline.

**Step 2:** Validate/normalize pagination metadata before treating an empty page as authoritative. Keep genuinely empty page-zero responses complete only with consistent zero metadata.

**Step 3:** Run crawl-completeness and competitor-monitor tests plus compileall.

**Step 4:** Commit only C3 code and test.

### Task 4: C4 — distinguish foreign and unresolved winner identity

**Files:**

- Modify: `crawler/scripts/run_all_exchange_competitor_monitor.py:25-67`
- Modify: `crawler/scripts/monitor_competitor_awards.py:225-262`
- Modify: `crawler/tests/test_ebirja_winner_identity.py`
- Modify: `crawler/tests/test_competitor_award_monitor.py`

**Step 1:** Add regressions: valid foreign INN remains a complete exclusion; missing/invalid INN produces partial identity, preserves prior state and still emits verified new awards.

**Step 2:** Return confirmed/rejected/unresolved identity counts. Convert unresolved identity to `partial_identity`; in delta construction union confirmed current awards with prior state, mark it not fully reported, and never remove old keys because identity is unknown.

**Step 3:** Run identity, monitor, completeness and all C1–C5 contract scripts; run compileall.

**Step 4:** Commit only C4 code and tests.

### Task 5: Integration, delivery and release verification

**Files:**

- Modify: `docs/audits/2026-09-22-c1-c5-integration-review.md` only if a factual status needs updating.

**Step 1:** Run the seven original mocked probes against final code. Their old failure assertions must be replaced with final success assertions or equivalent targeted tests; no stale diagnostic test may be reported as a pass.

**Step 2:** Run the complete offline C1–C5 suite from a clean checkout and inspect the diff against `main`.

**Step 3:** Push each release as a separate PR, merge sequentially, deploy the individual release to `/opt/parsing-seo`, and repeat the affected offline tests on VPS. Do not run a live full crawl or send a live Telegram report as a test.

**Step 4:** Update the audit/context only with verified results and report any remaining external limitations separately.

---

## Продолжение существующего плана: восстановление и эксплуатационная приёмка

Обновлено 22.09.2026 после проверки реально работающей цепочки.
Это продолжение C1–C5 и B1–B4, а не новый глобальный план.

> **For Codex:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this continuation task-by-task. Work sequentially; no agent swarm.

**Goal:** Восстановить полный обход, проверить данные за период сбоя и принять
весь контур по наблюдаемым результатам, включая все девять строк бирж.

**Architecture:** Сохранить общий путь активных алертов и отдельный монитор
побед. Приёмка охватывает новые записи, обновление detail, повторный отбор,
состояние источников и доставку; каждый кодовый выпуск проверяется отдельно.

**Tech Stack:** Существующий Python/standalone tests, Supabase, VPS cron,
локальные JSON state/outbox, Git PR. Новые сервисы и миграции не предполагаются;
их необходимость, если возникнет, обосновать конкретным дефектом.

### Факты и границы исходного состояния

- На VPS при последнем чтении `b803faa`; успешные 62 offline-теста не закрыли
  случай новой записи без сохранённого detail.
- 22.09 в 16:02:36 UTC полный обход завершился AttributeError в db.py:253:
  13 804 fetched, 0 upserted, 0 alerts. Это не число потерянных возможностей.
- Feedback-процесс активен, но healthcheck сообщает STALE CODE.
- Новые C3–C5 уже merged; живую доставку последней версии ещё не подтвердили.
- Пункты 1–4 выше сохраняются как история исполненных выпусков. Ограничение
  прежнего Task 5 на live verification заменяется следующими воротами приёмки:
  сначала offline, затем наблюдение штатных запусков; один контролируемый
  живой запуск допустим при необходимости и в рамках согласованного внедрения.
- Сейчас пользователь запросил план: этот документ не означает исправление,
  запуск краулера, перезапуск сервиса или отправку Telegram.

### Общие правила исполнения и экономии

1. Работать в отдельной ветке `codex/…` от свежего main. Сначала сверить рабочее
   дерево, SHA VPS и текущий cron: состояние могло измениться после аудита.
2. До изменения зафиксировать SHA и проверенный способ отката. Для state,
   outbox и затронутых строк сохранять ограниченные backup/manifest. Не
   выгружать всю БД и не удалять прежние receipts/курсоры.
3. Один выпуск = конкретная ошибка, тест, commit, PR, merge, deploy, проверка
   на VPS. Main автодеплоится: merge уже запускает выкладку.
4. Тестировать изменённый контракт и соседний стык; один итоговый прогон набора
   затронутых тестов. Повторять только после изменений или новой ошибки.
5. Offline-пробы, replay без `--ai`, shadow с `--judge-limit 0` не требуют AI.
   `recheck` без `--execute` всё равно вызывает AI — не считать его zero-AI.
   Для ручной проверки AI сначала выбрать не более 10 действительно спорных
   кандидатов и зафиксировать бюджет; не дублировать dry-run AI и отправку
   без необходимости. Это лимит выборки, не обещание ровно 10 API-вызовов.
6. Все девять строк источников участвуют в каждом приёмочном цикле. Делать
   ограниченные свежие проверки доступности/схемы; использовать уже сохранённый
   снимок для offline-regression. Полный архив не пересобирать при каждом фиксе.
   Повторно использованный receipt явно маркировать датой; он не доказывает
   свежую доступность. При внешнем блокере сохранять статус, а не ставить PASS.
7. Не менять общий порог production-алертов ради конкурентной выборки >20 млн.
   Не продвигать shadow-слова автоматически, не сбрасывать AI-оценки массово.

### Task 6 / выпуск A: устранить остановку полного обхода (C2, P1)

**Files:** Modify `crawler/core/db.py`; Test `crawler/tests/test_detail_text_persistence.py`.

**Step 1.** Дописать регрессии на публичный `upsert_tenders` с fake-клиентом БД:
новый лот без detail, новый с detail, новый prequal с lots, существующий со
старым detail, смешанный пакет с обычным источником. Проверять состав записанных
строк и new_tenders, а не только отсутствие исключения.

**Step 2.** Убедиться, что новые сценарии падают на текущем коде. Минимальная
правка нормализует отсутствующую старую строку:

```python
row = existing_rows.get((tender.external_id, tender.source)) or {}
stored = row.get("extra_info") or {}
```

Не добавлять общий `except: pass` вокруг сохранения. Сохранить свежий detail,
legacy-восстановление, приоритет свежих lots и defer после failed lookup.

**Step 3.** Локальные команды из корня code repo:

```bash
PYTHONPATH=. python3 crawler/tests/test_detail_text_persistence.py
PYTHONPATH=. python3 crawler/tests/test_alert_detail_contract.py
PYTHONPATH=. python3 crawler/tests/test_prequal_detail.py
```

**Expected:** новые случаи сначала воспроизводят отказ, после правки все проходят;
один list-only лот не блокирует записи других источников, повтор не создаёт new.

**Step 4.** Отдельный commit/PR, merge и deploy. На VPS повторить затронутые
offline-тесты и сверить SHA. Проверить, что тесты используют fake БД/Telegram.

**Step 5.** Актуализировать `parsing-feedback-bot` штатным restart без удаления
webhook, токенов и pending updates. Проверить active, время нового процесса,
успешный polling и отсутствие STALE CODE. Не поднимать выведенный Docker crawler.

**Gate A:** первый полный API-обход после выкладки проходит участок сохранения
и алертов; записи действительно обновляются. Lite/TG-прогон не подменяет full.
Нет pipeline traceback и прежних FAIL `freshness.full_api`/`feedback_bot`;
внешние ошибки источников перечислены отдельно. Если новых подходящих лотов
нет, ноль алертов допустим; количество алертов не искусственная цель.

**Rollback:** при новой регрессии остановить следующие выпуски, применить
минимальный обратный commit к неудачному изменению либо проверенный рабочий
релиз; не откатываться автоматически к b803faa — он заведомо неисправен.
State/outbox не откатывать вместе с кодом, чтобы не повторить доставку.

### Task 7 / выпуск B: восстановить последствия сбоя и путь позднего detail (C2)

**Files:** Inspect `crawler/adapters/api.py`, `crawler/core/db.py`,
`crawler/core/prequal_detail.py`, `crawler/scripts/recheck.py`;
Test `crawler/tests/test_active_detail_search_text.py`,
`crawler/tests/test_detail_text_persistence.py`, `crawler/tests/test_recheck.py`.
Create if needed `crawler/scripts/recover_detail_gap.py` and its focused test.

**Step 1.** По журналу определить первый и последний упавшие полные обходы.
Сверить курсоры, источники, сохранённые ID/detail и текущие активные лоты.
Не использовать collected_at как надёжную дату первого появления: оно обновляется.
Сохранить manifest конкретных `(source, external_id)` с основанием восстановления.
Если лот исчез из открытой выдачи и нет receipts, отметить unknown.

**Step 2.** Проверить сценарий «detail получен → cursor сохранён → upsert упал →
повтор». Если подтверждён пропуск повторного detail, сделать минимальный
сохраняемый повтор для конкретных ID, независимый от high-water cursor, либо
перенести подтверждение обработки за успешное сохранение с сохранением нынешних
ограничений нагрузки. Выбор зафиксировать по коду до правки; глобально курсор
не обнулять. Ограниченный ремонт исторических данных не заменяет защиту от
повторения этого сбоя.

**Step 3.** Восстанавливать только manifest-ID: сначала dry-run и backup полей,
затем ограниченные пакеты. Собрать предмет/detail/lots без изменения feedback,
alert_seq, идентичности и остальных полей. Сохранять failed/pending для retry.

**Step 4.** Проверить поздний detail для уже существующей строки. Сейчас recheck
берёт только `relevance_score IS NULL`: лот, ранее отвергнутый AI по бедному
тексту, туда не попадёт. При подтверждении пропуска добавить явный ограниченный
повтор для изменившегося detail, только для ещё не доставленных активных лотов.
Сохранять признаки повторной обработки; повтор без нового detail не вызывает AI.
Не сбрасывать relevance_score всем записям и не отправлять через обход notifier.

**Step 5.** Приёмочная последовательность: общий заголовок → поздний
«картонный картхолдер» → сохранение → повторный list-crawl → повторный отбор →
обычные gates/dedup → уведомление или объяснённый отказ. Отдельно проверить
лот, ранее уже оценённый AI. Offline-отправитель фиксирует количество вызовов;
живую проверку проводить на подходящем реальном кандидате без фальшивых тендеров.

```bash
PYTHONPATH=. python3 crawler/tests/test_active_detail_search_text.py
PYTHONPATH=. python3 crawler/tests/test_detail_text_persistence.py
PYTHONPATH=. python3 crawler/tests/test_recheck.py
PYTHONPATH=. python3 crawler/tests/test_replay_pure.py
```

**Gate B:** все ID manifest получили terminal outcome: восстановлен, уже
сохранён, недоступен/неизвестен с причиной. Пока остаются pending-ID,
восстановление не закрыто; недоступные/неизвестные ID отражены как ограничение,
а не как успешно восстановленные данные.
Пройден двухпроходный контракт detail; повторный отбор не дублирует отправленное.
Если нет живого подходящего события, live delivery остаётся pending — offline
успех не выдавать за доставку. Откат восстановления — только по backup конкретных
полей с проверкой, что более свежие изменения не будут затёрты.

### Task 8 / выпуск C: контроль здоровья действительно видит отказ (B3)

**Files:** Modify `crawler/core/runner.py`; inspect `crawler/core/crawl_logger.py`,
`crawler/scripts/healthcheck.py`; Test `crawler/tests/test_full_crawl_observability.py`.

**Step 1.** Воспроизвести адаптер, который вернул строки/пустой список, но сохранил
`last_error`. Сейчас runner пишет это в outcomes, но не передаёт в log_source_result.
Добавить поведенческий тест logger/runner, не ограничиваться поиском строк в AST.

**Step 2.** Передать ошибку в журнал; отличать здоровую пустоту, частичный результат,
auth_required, лимит сбора и исключение. Проверить, что свежий TG/lite запуск не
маскирует неуспешный полный API-профиль.

```bash
PYTHONPATH=. python3 crawler/tests/test_full_crawl_observability.py
```

**Gate C:** ошибки видны в source receipt, crawl run и healthcheck согласованно;
здоровая пустота не вызывает ложный FAIL. Штатное уведомление о проблеме срабатывает
и не спамит повторами. Исчезнувший endpoint не считается «нулём новых закупок».

### Task 9 / выпуск D: принять all-exchange weekly и восстановление доставки (C3–C5, B2/B4)

**Files:** `crawler/scripts/run_all_exchange_competitor_monitor.py`,
`crawler/scripts/monitor_competitor_awards.py`,
`crawler/scripts/enrich_competitor_award_specs.py`,
`scripts/run_competitor_award_weekly.sh`;
Tests `test_competitor_award_monitor.py`, `test_competitor_crawl_completeness.py`,
`test_ebirja_winner_identity.py`, `test_competitor_award_spec_enrichment.py`.
Менять только при воспроизводимом нарушении ниже.

**Step 1.** Для каждого из девяти источников обновить receipt с observed_at,
режимом проверки, доступностью, полнотой, лимитами и полями победителя/валюты.
Паспорт неизменен по составу: ETender Deals, UZEX Direct, Ebirja shop/auction/
tender/selection, Cooperation, XT-Xarid, Hayotbirja. Для XT/Hayot добавить
ограниченную свежую проверку публичной выдачи; статическая строка не означает,
что площадку проверили в этом запуске. Mirror подтверждать свежим наблюдением.

**Step 2.** Прогнать сохранённые входы: полный пустой результат; противоречивая
пагинация; page/detail cap; временный отказ; неизвестный и чужой ИНН. Отказ одного
источника сохраняет его историю и не блокирует подтверждённые события других.
Источники без ИНН/валюты остаются ограниченными, не «успешно нашли ноль».

**Step 3.** Проверить B2: спецификация была → detail временно недоступен → вернулся
тот же detail. Не должно быть ложных changed-awards. Подтверждённая новая
спецификация по-прежнему создаёт изменение. Отсутствие данных не заменять пустотой.

**Step 4.** Fake sender + временный каталог: 11+ событий, сбой второй части,
рестарт, успешный retry, pending вне 30-дневного окна, сбой checkpoint после send.
Проверить и чтение/запись state/outbox, и exit code. Durable pending не пропадает;
повтор при неопределённом подтверждении документируется как at-least-once.
Не внедрять искусственный сбой в production Telegram.

```bash
PYTHONPATH=. python3 crawler/tests/test_competitor_award_monitor.py
PYTHONPATH=. python3 crawler/tests/test_competitor_crawl_completeness.py
PYTHONPATH=. python3 crawler/tests/test_ebirja_winner_identity.py
PYTHONPATH=. python3 crawler/tests/test_competitor_award_spec_enrichment.py
bash -n scripts/run_competitor_award_weekly.sh
```

**Step 5.** Проверить один текущий all-exchange снимок preview на копии state/outbox.
Затем принять штатный weekly либо один контролируемый запуск под тем же flock,
с production-state и без смены baseline. Не запускать параллельно cron.
Сверить SHA, receipt, новые/изменённые ключи, принятые Telegram-части, остаток outbox.
При наличии незавершённой очереди повтор должен реально читаться следующим запуском.

**Gate D:** все девять строк свежо проверены или имеют явный сбой/блокер;
нет потери pending и необоснованного удаления baseline. Каждое подлежащее
доставке событие либо подтверждено, либо остаётся pending с причиной.
Если событий нет, проверен пустой цикл, а фактическая доставка остаётся непроверенной.
Недоступные поля — ограничение охвата, не основание бесконечно расширять разработку.

### Task 10 / выпуск E: строгий shadow >20 млн и проверяемая польза (B1/B5)

**Files:** Modify `crawler/scripts/shadow_search.py`,
`crawler/tests/test_shadow_search.py`; reuse `crawler/core/competitor_audit.py`.
Проверить фактическое место scheduled shadow-вызова перед изменением расписания.

**Step 1.** Добавить отдельный strict competitor режим с reuse `above_threshold`:
19 999 999 → нет; 20 000 000 → нет; 20 000 001 UZS → да; неизвестная сумма или
валюта → unknown, отдельно от matched и rejected. Общий production gate не менять.
Добавить currency в выгрузку shadow, если поля нет; не предполагать UZS по молчанию.

**Step 2.** Проверить контекст yoriqnoma, blank*, jurnal, gazeta, matbaa на
замороженных положительных/отрицательных примерах, включая совпадение только
в организации. Семантическое решение по сложным примерам фиксировать вручную;
нулевой AI-прогон измеряет лексические совпадения, не точность AI и не доставку.

```bash
PYTHONPATH=. python3 crawler/tests/test_shadow_search.py
PYTHONPATH=. python3 crawler/tests/test_competitor_audit.py
```

**Step 3.** На одном сохранённом наборе и одной дате сравнить доступные состояния
до/после: найден на площадке → записан → detail сохранён → ключевой gate →
AI/прочие gates (только где есть доказательства) → фактически отправлен.
Сохранять идентичность лота, время и evidence на каждом этапе. Не подменять
историческую своевременность сегодняшним replay; если исторического снимка
нет, своевременность = unknown.

**Gate E:** строгая конкурентная выборка соблюдает валюту и >20 млн; цифры
воспроизводимы на одинаковых данных; caps и unknown показаны. Ни одно слово
не добавлено в боевой фильтр только потому, что встретилось у конкурента.

### Task 11: итоговая приёмка и закрытие существующих пунктов

1. После последнего изменения один раз прогнать объединённый набор тестов,
   затронутых A–E. На VPS повторять только релевантные smoke/контракты.
2. Подтвердить два последовательных штатных полных API-обхода после финального
   выпуска: корректный профиль, нет общего падения, сохранение и gates пройдены,
   известные источниковые ошибки не скрыты. Два цикла — ограниченный smoke,
   не доказательство вечной стабильности.
3. Подтвердить актуальный feedback-процесс и отсутствие регресса TG/Cooperation.
4. Закрыть manifest восстановления либо перечислить конкретные недоступные ID.
   Проверить один содержательный двухпроходный сценарий позднего detail.
5. Принять один настоящий weekly-цикл; второй — последующее наблюдение,
   без повторного полного аудита и лишнего AI. Если нет реальных событий,
   не закрывать пункт live delivery как проверенный.
6. Обновить BACKLOG/main/context по фактам: исправлено/выкачено/проверено вживую/
   ограничено внешним источником/pending. Сверить документы, а не оставить
   C1–C5 одновременно закрытыми в context и открытыми в BACKLOG.
7. Итоговая карточка приёмки: SHA, время, run_id, источник, сценарий, ожидаемое,
   фактическое, receipt/log, остаточные ограничения. Каждый PASS имеет evidence.

**Когда можно сказать «контур восстановлен»:** gates A–C пройдены и нет
неразобранного общего сбоя; восстановление и late-detail проверены.
**Когда можно сказать «мониторинг принят»:** дополнительно gates D–E, с честно
отмеченными live-pending и внешними ограничениями. «Все победы всех бирж ловим»
не заявлять без независимого полного эталона — эти ворота этого не доказывают.

**Порядок:** A немедленно → B → C → D → E → итоговая приёмка. Не откладывать
аварийный фикс до завершения исследовательских/исторических задач. Если B
упирается во внешне недоступные данные, сохранить доказанный blocker и продолжать
независимые C–E; зависимые критерии B не объявлять выполненными.
