# ТЗ — Classifier Playbook Loop (самообучение по образцу Nyro Атом 6)

> Дата: 2026-06-07 · Версия: **v2** (после red-team, см. §9 changelog)
> Проект: parsing-seo · Ветка: `parsing-seo-playbook-loop` (от свежего `origin/main`)
> Статус: к реализации — **сначала Phase 0 gate (§4.0), без него не стартовать**
> Источник: разбор архитектуры Nyro v0.9.2 (Claude Agent SDK / Agent Skills паттерн)

---

## 0. Job Story (AJTBD) — с честной границей охвата

**КОГДА** Данияр размечает тендерные алерты (`parsing_feedback_cli <N> client|ad|irrelevant`),
исправляя ошибки классификатора,
**→ я хочу**, чтобы классификатор учился на ОБОБЩЁННОМ уровне (признак ошибки), а не на
сырых последних 5 примерах и не через ручное дописывание regex,
**→ ЧТОБЫ** точность на **AI-слое** росла от разметки, без дообучения модели и без
накопления костылей-рельс.

**Граница охвата (честно, не прятать):** playbook действует ТОЛЬКО на ошибки, которые
**доходят до AI-слоя**. Класс `demand-as-ad`, вызванный тем, что regex `_AD_FILTER`/
отсутствие `_DEMAND_PATTERNS` отсёк сообщение ДО AI, playbook **физически не лечит** —
оно никогда не доходит до промпта. Этот класс остаётся за `refine_patterns.py` (см.
§2.6, §4 шаг 5). Обещание «precision/recall растут сами» относится к AI-достижимым
ошибкам, не ко всему пайплайну.

---

## 1. Проблема (evidence-based, [факт из кода на VPS])

Классификатор GROUP-потока (`crawler/adapters/telegram_adapter.py`):
```
_DEMAND_PATTERNS (regex)  →  _AD_FILTER (regex)  →  AI intent-check с few-shot
```
few-shot = `crawler/core/feedback.py::get_few_shot_examples(n=5)`.

| # | Дефект | Где | Тип |
|---|--------|-----|-----|
| D1 | Сырой «блэклист»: 5 коррекций текстом 1:1, привязка к формулировке | `feedback.py` | [факт мех-ма] |
| D2 | Урок старше 5-го выпадает из окна | `.limit(5)` | [факт мех-ма; **влияние — гипотеза**, проверить на объёме §4.0] |
| D3 | Путь обучения = дописывать regex | `refine_patterns.py` | [факт] — против `feedback_no_kostili_rails` |

> ⚠️ D2 — это механика, а не доказанный вред. Если 5 последних коррекций репрезентативны,
> «потеря» не вредит. Поэтому Phase 0 (§4.0) ОБЯЗАТЕЛЕН до стройки.

---

## 2. Решение — Playbook Loop

### 2.0 Почему это НЕ «рельсы в БД» (ответ на главную атаку red-team)

Честная граница (без неё ТЗ нельзя принимать):
- **Рельса (запрещено):** жёсткое детерминированное правило, написанное инженером «на
  всякий случай» / под конкретную сущность, живёт в коде, не вытекает из данных
  (regex `домен X = реклама`, hardcoded fallback, homoglyph fold).
- **Playbook-принцип (допустимо):** обобщение РЕАЛЬНОГО человеческого фидбека, выраженное
  как мягкий контекст для LLM (не жёсткий гейт), ревьюится человеком до активации,
  override-слой над промптом, код не трогает.
- **Граница, которую нельзя переходить:** принцип НЕ должен превращаться в детерминированный
  фильтр (типа «если текст содержит X → ad»). Если принцип хочется так сформулировать —
  это сигнал, что нужен regex/`prompt_proposals`, а не playbook-строка. Принцип = «как
  думать», не «что заблокировать».
- **Риск признаётся:** мутабельная LLM-генерируемая таблица менее инспектируема, чем
  regex. Митигация — ревью перед `active`, `≥2 corroborating corrections` (§2.2),
  детерминированный `signal_key` (§2.2), лимит ≤20.

### 2.1 Таблица Supabase `classifier_playbook`
```sql
classifier_playbook (
  id              bigint generated always as identity primary key,
  taxonomy        text not null,          -- §2.3
  principle       text not null,          -- обобщённый, БЕЗ имён собственных
  example         text,                   -- "(пример: ...)"
  signal_key      text not null,          -- ДЕТЕРМИНИРОВАННЫЙ ключ дедупа (§2.2)
  status          text not null default 'candidate', -- candidate | active | retired
  support_count   int  not null default 1, -- сколько коррекций подтверждают принцип
  retired_reason  text,
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
)
```
RLS обязателен (`supabase-security`): SELECT/INSERT/UPDATE true; DELETE только service_role.
**Не удалять — `retired`.** `unique(signal_key)`.

> Убраны: `hits` (red-team #7: инкрементить честно нельзя — промпт-инъекция не знает,
> какой принцип «сработал»). Контроль качества — через `support_count` + ревью, не hits.
> Добавлен `status='candidate'` — принцип не идёт в промпт, пока человек не апрувнул ИЛИ
> `support_count >= 2` (см. §2.2).

### 2.2 Weekly-джоба `playbook_refine`
```
1. fetch_feedback(period) из alert_feedback (переиспользовать из refine_patterns.py).
2. Для каждой коррекции LLM:
   a. классифицирует по таксономии §2.3;
   b. формулирует обобщённый принцип БЕЗ имён (линтер §2.4);
   c. возвращает signal_key ДЕТЕРМИНИРОВАННО: `taxonomy + ":" + slug(нормализованный
      признак)`. Нормализация — фиксированная (lower, trim, без пунктуации). LLM
      предлагает короткий signal-slug из закрытого набора признаков, НЕ свободный текст.
3. Дедуп по signal_key (UNIQUE): есть → support_count += 1, при необходимости усилить
   формулировку (UPDATE). Нет → INSERT как 'candidate'.
4. Промоут: candidate → active, когда support_count >= 2 ИЛИ человек апрувнул в ревью.
   support_count == 1 остаётся 'candidate' (НЕ влияет на промпт) — лечит laundering
   одиночного шума (red-team Атака A).
5. Противоречие активному принципу (новая коррекция инвертирует) → старый → 'retired'
   + retired_reason, новый сигнал по своему пути. НЕ удалять.
6. Системный пробел (нужен новый источник/поле/regex — playbook не лечит, особенно
   prefilter-FN класс §0) → НЕ трогать код, отчёт `prompt_proposals` Данияру (TG --send).
```
Модель: сильная (deepseek-v4-pro через текущий OpenRouter-ключ), **не flash**
(`feedback_no_dumb_model_for_savings`).

### 2.3 Таксономия
| Тег | Смысл | Лечит playbook? |
|-----|-------|-----------------|
| `ad-as-demand` | Реклама принята за спрос (FP) | ✅ если дошло до AI |
| `demand-as-ad` | Спрос отклонён как реклама (FN) | ⚠️ ТОЛЬКО если дошло до AI; если отсёк regex → `prompt_proposals` |
| `irrelevant-niche` | Вне ниши (полиграфия/упаковка) | ✅ |
| `wrong-extraction` | Intent верный, поля/title кривые | ✅ |
| `trivial` | Разовая мелочь — НЕ возводить в принцип (дропать) | — |

> Убран `source-specific` (red-team: противоречит линтеру «без имён» — источник = имя
> собственное). Если паттерн реально привязан к ТИПУ источника — обобщать по типу
> («каналы-агрегаторы рекламных постов»), иначе → `prompt_proposals`. `trivial` поглощает
> бывшие «псевдо-принципы разное».

### 2.4 Линтер принципа (ключевое правило Nyro)
Принцип — по обобщаемому признаку, **без имён собственных** (компаний/каналов/доменов/
продуктов). Конкретика — только в `example` как «(пример: …)».
- ❌ «Домен upakovka-x.uz — реклама»
- ✅ `[ad-as-demand]` «Прайс + призыв к действию (контакты, "под заказ", "обращайтесь")
  без глагола поиска ("ищу"/"нужен"/"кто делает") = реклама, даже если упомянут наш
  товар (пример: пост с ценой на коробки + "звоните").»

Проверка: имя собственное в `principle` → джоба переписывает через признак, иначе строку
не сохраняет.

### 2.5 Встройка в классификатор + судьба few-shot (решено сейчас, не «через 2 недели»)
- `feedback.py::get_playbook() -> str`: читает `status='active'`, форматирует, кэш как
  `_FEW_SHOT_TTL` (2 ч), лимит ≤20 принципов.
- `telegram_adapter.py`: новый плейсхолдер `{playbook}` в `_AI_EXTRACT_PROMPT` рядом с
  `{few_shot}`; в `_parse_group_message` перед AI: `playbook=get_playbook()`, проброс в
  `_ai_extract_fields`.
- **Разделение ролей (явная precedence, не дублирование — red-team over-eng):**
  - `few_shot` = **свежий сырой** сигнал (последние 5, как есть) — «горячие» примеры;
  - `playbook` = **обобщённый устойчивый** сигнал — override при конфликте.
  - В промпте явно: «при конфликте принципы playbook важнее единичных примеров».
- **План отключения few-shot — явный шаг, не "мониторить":** после того как playbook
  наберёт ≥5 active принципов И пройдёт регресс §5, провести A/B (`run_eval`-стиль):
  few-shot+playbook vs только playbook. Если только-playbook ≥ комбо — убрать few-shot
  отдельным коммитом. Решение по числу, не по ощущению.

### 2.6 refine_patterns.py — НЕ убивать
`refine_patterns.py` остаётся единственным каналом для prefilter-FN класса (§0), который
playbook не достаёт. Перевести в **read-only метрику** (предлагает regex человеку, не
авто-применяет) — оставить cron. НЕ заменять на playbook_refine, а **дополнить**.

---

## 3. Anti-scope
- НЕ трогаем regex `_DEMAND_PATTERNS`/`_AD_FILTER` (дешёвый пре-фильтр до AI).
- НЕ дообучаем модель. НЕ генерим regex авто. НЕ трогаем `ai_evaluator.py`.
- НЕ меняем `parsing_feedback_cli.py` и схему `alert_feedback` (только читаем).
- **Phase 1 = только GROUP telegram-поток.** Биржевая relevance = Phase 2.
- Принцип НЕ становится детерминированным фильтром (§2.0).

---

## 4. Фазы

### 4.0 Phase 0 — GATE (обязательно ДО кода, red-team #1/#3/missing-context)
```sql
select count(*), min(created_at), max(created_at) from alert_feedback;
select corrected_label, count(*) from alert_feedback group by 1;
```
- **< ~30 коррекций всего** → playbook ПРЕЖДЕВРЕМЕНЕН. Не строить полный контур;
  максимум — увеличить окно few-shot и улучшить отбор примеров. Зафиксировать решение.
- **≥ 30** → строить. Зафиксировать baseline: `get_feedback_stats(30)` (FP/FN/accuracy) +
  объём для оценки статзначимости (§5).

### 4.1 Phase 1 (при прохождении gate)
1. Миграция `classifier_playbook` + RLS (следующий номер после 017).
2. `feedback.py::get_playbook()` + кэш + лимит ≤20.
3. `telegram_adapter.py`: `{playbook}` в промпт + проброс.
4. `scripts/playbook_refine.py` (переиспользовать `fetch_feedback`); детерминированный
   `signal_key`; candidate/active промоут по `support_count>=2`; retired при противоречии;
   `prompt_proposals` отчёт.
5. Cron: добавить `playbook_refine --send`, `refine_patterns` оставить read-only (§2.6).
6. Bootstrap на истории: прогнать, но всё садить как `candidate`; в `active` поднимать
   ТОЛЬКО при `support_count>=2` или ручном апруве (не laundering одиночек).

### Phase 2 (отдельное ТЗ): playbook для биржевой relevance (cooperation/uzex),
центр — `irrelevant-niche` (Баннер/Стенд/чек-лента из main.md Watch).

---

## 5. Acceptance (исправлено: каузальность + статзначимость)
Baseline — §4.0.
- [ ] Таблица + RLS (Security Advisor чисто). `unique(signal_key)` работает.
- [ ] Bootstrap: ≥3 `active` принципа, у каждого `support_count>=2`, **ни одного имени
      собственного** (ручная проверка линтера), каждый процитирован к ≥2 коррекциям.
- [ ] `get_playbook()` реально в промпте: лог одного AI-вызова в
      `parsing-seo-ai-decisions.jsonl` содержит текст принципов.
- [ ] **Регресс-тест с A/B-дельтой (red-team #1/#2):** взять исторические коррекции где
      `original≠corrected` И где `message_text` присутствует. Прогнать через **полный
      пайплайн** (regex→AI), НЕ изолированный `_ai_extract_fields`. Сравнить:
      без playbook vs с playbook на ИДЕНТИЧНОМ сете. Засчитывается **дельта** (playbook
      исправил ≥ X примеров, которые база не исправляла), а не абсолютные ≥70%. Ожидаемо:
      prefilter-убитые примеры дают 0 улучшения (§0) — это нормально, исключить из
      знаменателя AI-достижимых.
- [ ] Идемпотентность: повторный `playbook_refine` не плодит дубли (`signal_key` UNIQUE).
- [ ] Противоречие: обратная коррекция → старый принцип `retired` + reason, не удалён.
- [ ] Латентность/токены (red-team): измерить дельту времени AI-вызова с ≤20 принципами
      vs без. Порог: не ухудшать p95 значимо (main.md: qwen отклонён за 42с p95).
- [ ] ~~accuracy через 2 недели~~ **УДАЛЕНО** — при объёме фидбека одного человека
      (single-digit/период) `get_feedback_stats(14)` = шум. Замена: качественное ревью —
      каждый `active` принцип осмотрен Данияром, `support_count>=2`.

---

## 6. Риски
| Риск | Митигация |
|------|-----------|
| LLM обобщает широко → режет валидный спрос | `candidate` пока `support_count<2`; ревью до active; `example` обязателен |
| Bootstrap из старого шума → ложные принципы | Всё в `candidate`; в active только `>=2` подтверждений (red-team Атака A) |
| Playbook раздувает промпт / латентность на realtime-пути | Лимит ≤20; измерить дельту p95 (acceptance) |
| Конфликт playbook↔regex (prefilter-FN) | playbook не покрывает (§0); канал — `refine_patterns`/`prompt_proposals`, не молчать |
| Playbook становится «рельсами в БД» | Граница §2.0: принцип = «как думать», не «что блокировать»; ревью; детерм. signal_key |
| Двойной контур few-shot+playbook | Явные роли + precedence (§2.5); A/B и отключение few-shot по числу |
| signal_key недедуп/переслияние | Детерминированный (taxonomy+slug), UNIQUE, закрытый набор признаков (§2.2) |

---

## 7. Дисциплина веток / деплой
- `git switch -c parsing-seo-playbook-loop origin/main` (свежий main).
- Код на VPS `/opt/parsing-seo`; деплой = merge в `main` → auto `git pull --ff-only`
  (cron */5). Контейнер пересобрать при изменении зависимостей.
- Миграция на Supabase `oaoehczbycrabkprazts` через Management API / PAT.
- После мержа — verify §5 на проде; дельта в `main.md` + `history.md`.

---

## 8. Pre-flight (fail-fast из red-team — выполнить ПЕРЕД кодом)
1. `select count(*) from alert_feedback` < ~30 → СТОП, см. §4.0.
2. Регресс-тест прибит к ПОЛНОМУ пайплайну + baseline-без-playbook на том же сете.
3. `signal_key` детерминирован до Phase 4 (иначе дедуп и retired — no-op).
4. Судьба few-shot решается числом (A/B), не «мониторингом».
5. `refine_patterns` cron НЕ убивать (prefilter-FN канал).
6. `accuracy через 2 недели` из acceptance удалён — не возвращать.

---

## 9. Changelog (red-team 2026-06-07, verdict NEEDS_REVISION → applied)
- §0: добавлена честная **граница охвата** (playbook ≠ prefilter-FN класс) [#7].
- §2.0: новая секция «почему не рельсы» — ответ на Атаку B.
- §2.1: `hits` удалён (fake control [#7]); добавлен `status='candidate'` + `support_count`.
- §2.2: `signal_key` сделан **детерминированным** [#4/#5]; промоут active только при
  `support_count>=2` [Атака A]; противоречие → retired (сохранено).
- §2.3: убран `source-specific` (конфликт с линтером); `trivial` поглотил «разное».
- §2.5: few-shot vs playbook — **роли + precedence решены сейчас** + A/B-план отключения [over-eng].
- §2.6: `refine_patterns` НЕ убивать — read-only метрика для prefilter-FN [missing-context].
- §4.0: новый **Phase 0 gate** — `count(*)` перед стройкой [#3/missing-context].
- §5: регресс-тест → **полный пайплайн + A/B-дельта** [#1/#2]; добавлен latency-порог;
  «accuracy через 2 недели» **удалён** как статистически пустой [#3].
- §8: pre-flight fail-fast чеклист.
