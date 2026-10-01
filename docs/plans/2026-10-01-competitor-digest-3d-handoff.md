# Сводка по конкурентам раз в 3 дня — пауза 01.10.2026 (лимит), как продолжить

Статус одной строкой: **PR 1 смержен и на проде; PR 2 написан, протестирован, запушен в ветку, но PR не открыт и не смержен; список конкурентов и крон не тронуты.**

## Чего хотел Данияр
Сводки каждые 3 дня по 25–30 конкурентам, чтобы быстро понимать и учиться, в том числе когда они «прячут» лоты. Решения: список — «ровно ~30 по данным»; токен ebirja при простое — запустить вручную.

## Сделано и проверено

| Что | Состояние |
|---|---|
| PR 1 `fix/civil-winner-inn` | Смержен (2c9183a), на проде. `results_tracker.format_winner`: победитель ВМК-69 как «Имя (ИНН 123)»; бэкфилл-скрипт; регэксп `winner_inn` понимает старый «ИНН: 123». Прогнан `/code-review high` (7 находок: 4 исправлены, 2 пропущены, 1 тест добавлен). |
| Рестарт бота | Сделан 11:20 UTC после PR 1 (правило «core → рестарт»). Нужен ещё раз после PR 2. |
| Бэкфилл `backfill_civil_winner_inn` | **Только dry-run, ничего не записано.** API отдал 9293 из 9293. На старой версии скрипта совпало лишь 667 из 4200 строк — причина ниже. |
| Ветка PR 2 | `feat/competitor-digest-3d`, запушена в origin одним WIP-коммитом. Тесты: 969 проходят в прямом и обратном порядке файлов, 4 падения `test_deploy_fresh` — только в git worktree (на основном чекауте зелёные). 4 мутации ключевых свойств пойманы. |
| Сухой прогон на проде | `--dry-run --days 30` из временной копии `/tmp/ps2` на VPS: окно 01.09–01.10, сделок 1271, итогов ВМК-69 659, профиль 20, «спрятанных» 8, AI 43/60. Блок «Список» и строка покрытия строятся. Строки монитора в нём нет (не передавал `--monitor-receipts`). |

## Важная находка: 24.09 в 10:04 API ВМК-69 сменил формат id
`display_id` был «2612 **00** <id>», стал «2612 **05** <id>». В базе 3593 строки старого вида и 607 нового; **454 итога лежат дважды**. Поэтому:
- сшивка итогов — по ключу `CW.civil_norm_key` (префикс 4 + номер 8), а не по `external_id`;
- `CW.dedupe_civil` убирает дубли в сводке; в ранжировании фирм дедуп обязателен;
- бэкфилл тоже ходит по этому ключу, так что при `--apply` правок будет порядка 4000, а не 660.

## Что внутри ветки PR 2
- `crawler/core/competitor_wins.py`: второй фид (итоги ВМК-69: `parse_civil_win`, `clean_civil_row`, `civil_detail_url`, `civil_norm_key`, `dedupe_civil`), ритм `CADENCE` 3 дня и допуск `DUE_SLACK` 2 ч (`is_due`), блок «Список конкурентов» (`watch_map`, `watch_rows`), строка монитора (`summarize_monitor`), секция «Спрятанные», новая строка покрытия.
- `crawler/scripts/competitor_wins_weekly.py`: два фида, лоты ВМК-69 по `external_id`, здоровье обоих фидов, `--due` (exit 10 = рано), `--monitor-receipts DIR`, `read_monitor`.
- Монитор: `run_all_exchange_competitor_monitor.py --etender-covered-by-digest` (etender не собирается, статус `covered_by_digest`), `monitor_competitor_awards.py` (новый статус считается отчитавшимся, заголовок «Новые договоры конкурентов из списка»), шапка `scripts/run_competitor_award_weekly.sh`.
- `scripts/run_competitor_digest.sh` (due → монитор → сводка) и `ops/install-competitor-digest-cron.sh` (ставит `0 5 * * *`, убирает две недельные строки; идемпотентен, делает бэкап crontab).
- Тесты: `test_competitor_wins.py` (+19), `test_competitor_award_monitor.py` (+4), `test_competitor_wins_weekly_io.py` (новый, 7), `test_results_tracker_winner.py` (18).
- Не покрыто тестами: `build_report` целиком (нужна БД) — проверен только сухим прогоном на проде.

## Что осталось (по порядку)
1. **Открыть PR 2** из ветки `feat/competitor-digest-3d`, прогнать `/code-review high`, исправить, повторно отправить находки с `outcome`.
2. Смержить сам (`gh pr merge --merge --delete-branch`, правило Данияра: не спрашивать), дождаться pull на VPS (проверять `git log -1` в `/opt/parsing-seo`), **перезапустить `parsing-feedback-bot`** (план одобрен Данияром).
3. Бэкфилл: `cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.backfill_civil_winner_inn` (dry-run, убедиться в «9293 из 9293»), затем с `--apply`. Прод-DML: только после нового dry-run.
4. **Написать `crawler/scripts/competitor_watchlist.py --rank --days 120`** (ещё не написан): окнами по 30 дней гоняет `build_report`, считает победы по ИНН с дедупом ВМК-69, исключает 14-значные ПИНФЛ, ложные «типографии» по имени (PITNAK SHTAMP — автозапчасти) и перекупщиков книг для библиотек. Выдача — топ-30 по числу побед, при равенстве по сумме. Баланс OpenRouter был $8,01 — проверить перед прогоном (~300 вызовов flash, меньше $0,1).
5. **PR 3 (данные):** `crawler/config/competitor_registry.json` — ~30 активных фирм в `entities` (у каждой `sources` со ссылками на лоты и `basis` «N побед за 120 дн.»), выбывшие из старых 17 — в раздел `retired` с причиной (`load_registry` читает только `entities` и `separate_candidates`, лишний ключ безопасен). Пин-тест на реестр: валиден, ИНН из `retired` не попадают в `registry_inns`, в `entities` 25–32 записи.
6. **Установщик крона:** `bash /opt/parsing-seo/ops/install-competitor-digest-cron.sh` на VPS, сверить `crontab -l`. Пока его нет, в понедельник 05.10 сработают старые строки (04:00 монитор, 10:30 разбор) — уже на новом коде, это безопасно.
7. Живой прогон обёртки с `--force` в алерт-канал Данияра: монитор (etender = `covered_by_digest`), затем сводка; доставка = 200 в логе и сообщение в канале. Ожидаемо: первый прогон монитора по расширенному списку пришлёт договоры новых ИНН за 30 дней одним сообщением.
8. Документация в main Second Brain: `Projects/parsing-seo/main.md` (строка «конкуренты», ключевые файлы, «Открыто»), `changelog.md` (замер 01.10: из 50 профильных лотов 45 мы алертили, рынок дробный, смена формата id 24.09 и 454 дубля), `history.md`.
9. Итог Данияру: что проверено фактами, что нет.

## Шаг 0 — токен ebirja
Токен получен 20:00 UTC 30.09, истёк в 01:00 UTC 01.10; прогоны авторизации в 00, 04, 08 его не обновили, хотя API ebirja живой. Следующий прогон — 12:00 UTC. Проверить только поле `obtained_at` у `crawler_settings.auth_token:ebirja` (самого токена не выводить). Если не обновился — запустить `/opt/eimzo/run_auth.sh` один раз вручную (разрешено Данияром) и проверить `obtained_at` и healthcheck. Если классификатор не даст — отдать Данияру готовую команду.

## Сводка по рынку (замер 01.10, etender с 11.06, гейт + AI)
72 чужие победы у 38 фирм; две и больше побед у 12; из 50 профильных лотов 45 мы алертили. Лидеры: Pechatnik Vostoka (9, 1,55 млрд), Matrix (7), Oltin-Nashr, G'.G'ulom nashriyot, E-Media-Agency, Premium Poligraf. Спрятанные лоты: «Poligrafiya…», «Nashrlarni chop etish xizmati», журналы.

## Состояние окружения
- Код-репо: `/Users/doniersalahutdinov/tmp-parsing-seo`. Рабочий worktree лежал в каталоге временных файлов сессии и может исчезнуть — **источник правды — ветка `feat/competitor-digest-3d` в origin**. Новый worktree: `git -C /Users/doniersalahutdinov/tmp-parsing-seo worktree add -b feat/competitor-digest-3d <путь> origin/feat/competitor-digest-3d`.
- `.venv/bin/python` (3.9.6) лежит в основном чекауте `tmp-parsing-seo`.
- На VPS осталась временная копия `/tmp/ps2` (сухой прогон) и скрипты `/tmp/rank_competitors*.py`, `/tmp/dbg_civil*.py`, `/tmp/rank_competitors*.json` — можно удалить с «да» (Tier 3).
- Ничего не запущено в фоне, крон не менялся, бэкфилл не применён, Telegram ничего не отправлено (сухие прогоны печатают в консоль).
- Ветка Codex `codex/weekly-side-effect-doc` и папка `.worktrees/parsing-seo-competitor-audit` в Second Brain не тронуты (их комментарий уже учтён в `run_competitor_award_weekly.sh`).
- Правила: мержить PR самому; прод-DML только скриптом с dry-run; Python 3.9; Tier 3 (удаление, restart, правка токенов) — по «да», кроме рестарта бота после core-правок (одобрен планом).
