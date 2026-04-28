# Parsing-SEO 2.0 — AI-Native Engine

Концептуальный документ. Что менять, чем заменять, в каком порядке. Базируется на Anthropic "Building Effective Agents", курсах, Agent SDK, моделях 4.6/4.5/4.7.

## TL;DR

Текущий parsing-seo — это **процедурный workflow**, не агент: 88 YAML-источников, фиксированные адаптеры, AI используется в 2 местах (Qwen-30B для enrichment + daily evaluator). Это **уже правильное решение** для большей части пайплайна — не надо ломать. Но 4 узла действительно выиграют от агентских паттернов: relevance filter, schema repair, anomaly RCA, Bitrix enrichment. Для них — Anthropic SDK + caching + Sonnet/Haiku mix. Остальное (HTTP, парсинг, dedup, dispatch) — оставить чистым Python. Главный принцип Anthropic: *"add complexity only when it demonstrably improves outcomes"* ([engineering blog](https://www.anthropic.com/engineering/building-effective-agents)).

## 1. Архитектурная карта — какие паттерны применимы

| Anthropic паттерн | Сейчас | Применять? | Где в parsing-seo |
|---|---|---|---|
| **Prompt chaining** | Нет | ДА (узко) | tender → entities → Bitrix match → outreach draft. Каждый шаг с gate-валидацией |
| **Routing** | Keyword filter | ДА | `relevance_classifier`: tender → {relevant / ad / irrelevant} с reasoning. Замена `item_filter` для пограничных случаев |
| **Parallelization (sectioning)** | `asyncio.gather` 88 источников | УЖЕ ЕСТЬ | Не трогать. Это процедурный parallel — AI не нужен |
| **Parallelization (voting)** | Нет | НЕТ | Тендеры не нуждаются в multi-judge. Один classifier достаточно |
| **Orchestrator-workers** | `runner.py` диспетчер + N адаптеров | ЧАСТИЧНО ЕСТЬ | НЕ заменять на LLM-orchestrator — каждый запуск стоил бы $$. Применить только в `schema_repair` (см. §3) |
| **Evaluator-optimizer** | `ai_evaluator.py` (1x/день) | РАСШИРИТЬ | Замкнуть петлю: eval → improvement suggestions → автоматический PR в `sources.yaml` (с человеческим approve) |
| **Full agent** | Нет | ДА (1 место) | `source_discovery_agent` — раз в неделю ищет новые площадки в open web |

**Что НЕ применять:**
- LLM-роутер на каждый источник (88 LLM-вызовов на цикл = $$$, latency, fragility). YAML-роутинг + статический dispatch уже оптимален.
- LLM-orchestrator над всем crawl. Anthropic явно: *"agents are not always the answer; for cost-sensitive applications, predefined workflows are better"*.

## 2. Stack rec — модели и инфраструктура

| Задача | Модель | Почему | Cost оценка |
|---|---|---|---|
| Relevance classifier (массовая) | **Haiku 4.5** | 90% качества Sonnet 4.5 на agentic, 80-120 tok/s, $1/$5 per Mtok ([Anthropic](https://www.anthropic.com/news/claude-haiku-4-5)) | ~500 тендеров/день × 800 tok = $0.40/день |
| Enrichment (extract entities) | **Haiku 4.5** | structured output, простая задача | заменит текущий Qwen-30B ($0.003/день → ~$0.10/день) |
| Schema repair agent | **Sonnet 4.6** | reverse-engineering API, multi-step reasoning | редко (раз в неделю при поломке), $0.05/инцидент |
| Bitrix enrichment chain | **Sonnet 4.6** | tender → company match → contact → email draft, нужно reasoning | $0.20 на топ-10 тендеров/день |
| Anomaly RCA | **Sonnet 4.6** | "почему UZEX упал на 80%?" — гипотезы | 1x/день на регрессии, $0.05 |
| Source discovery | **Sonnet 4.6** + WebSearch tool | open-ended, нужен tool use | 1x/неделю, $0.50 |
| Daily quality eval | **Haiku 4.5** | сейчас Qwen, паттерн уже работает — мигрировать | $0.02/день |
| Opus 4.7 | **НЕ оправдан** | 6x дороже Sonnet 4.6 без выигрыша на этих задачах | — |

**Total budget after migration:** ~$1-2/день (~$30-60/мес) против текущих копеек на Qwen. Оправдано если relevance precision вырастет с keyword-match (~70%) до ~95%.

**SDK choice:** **Direct Anthropic SDK** (`anthropic` Python lib), не `claude-agent-sdk-python`. SDK заточен под Claude Code-style сессии (file ops, bash). Для backend-агентов с custom tools — прямой `messages.create()` чище. Agent SDK оверкилл когда не нужен MCP-сервер. (Источник: [SDK README](https://github.com/anthropics/claude-agent-sdk-python) — основные примеры про CLI-сессии).

**Prompt caching стратегия:**
- 5-min TTL: 1.25× write, 0.1× read. 1-hour TTL: 2× write, 0.1× read ([Anthropic docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)).
- Кэшировать: tool schemas, system prompts с keyword-таксономией, примеры few-shot.
- Не кэшировать: тело тендера (всегда уникально).
- Для batch-classifier: один cache-hit prompt, 500 разных tenders → экономия 90% input cost.

## 3. Subagents map — какие реально нужны

Не все 6 предложенных оправданы. Приоритизация:

### Must-have (P1)

1. **Relevance Classifier Agent** (Haiku 4.5, single-call, не agent в строгом смысле)
   - Input: title + search_text
   - Output: `{relevant: bool, category: "packaging|printing|other", confidence: 0-100, reason: str}`
   - Заменяет `item_filter` для тендеров с неоднозначным title ("оборудование для производства упаковки" — keyword "упаковка" сработает, но это не наш заказ).
   - Где: новый `crawler/core/ai_classifier.py`, hook после `dedup` перед `notifier`.

2. **Bitrix Enrichment Agent** (Sonnet 4.6, prompt chain)
   - Шаги: extract organization → search Bitrix companies → suggest deal/lead → draft outreach.
   - Использует MCP bitrix24 tools (уже есть на VPS).
   - Запуск: только для top-N релевантных тендеров после classifier (10-20/день, не 500).

### Nice-to-have (P2)

3. **Schema Adapter Agent** (Sonnet 4.6, on-demand, не cron)
   - Триггер: source падает 3+ цикла подряд (`zero_result_tracker.py` уже это отслеживает).
   - Действия: fetch raw response, diff с last known schema, propose YAML patch.
   - **НЕ автоисправление** — выдаёт PR/Telegram alert Данияру.

4. **Anomaly Detector Agent** (Sonnet 4.6)
   - Расширение текущего `ai_evaluator.py` от 3 рекомендаций до RCA: "UZEX упал на 80% — гипотезы: смена API / ban IP / DNS / regex поломка".
   - Подключить tools: `query_supabase`, `tail_logs`, `curl_endpoint`.

### Skip (P3, не делать)

5. **Source Discovery Agent** — звучит круто, но 88 источников уже покрывают рынок. Раз в полгода Данияр сам найдёт через TenderZone/Bnect быстрее чем агент.
6. **Quality Evaluator continuous loop** — текущий evaluator + ручной PR на `sources.yaml` достаточно. Автоматический self-tuning prompts = риск регрессий без человеческого review.

## 4. Migration path — НЕ rewrite

| Шаг | Что делаем | Время | Риск |
|---|---|---|---|
| 1 | Заменить Qwen→Haiku 4.5 в `enricher.py` (drop-in, тот же интерфейс) | 1ч | низкий — fallback на Qwen если ANTHROPIC_API_KEY не задан |
| 2 | Добавить `ai_classifier.py` параллельно с keyword filter (shadow mode 2 недели — логировать decisions без действий) | 4ч | низкий |
| 3 | Если precision >90% — переключить shadow→primary. Keyword остаётся fallback | 1ч | средний — нужен 2-недельный QA |
| 4 | Bitrix enrichment chain для top-10 тендеров/день | 8ч | средний — Bitrix API rate limits |
| 5 | Schema Adapter Agent (триггер от `zero_result_tracker`) | 6ч | низкий — output suggestion only |
| 6 | Расширить `ai_evaluator.py` до anomaly RCA с tools | 4ч | низкий |

**Что НЕ трогаем:** `runner.py`, `adapters/*.py`, `dedup.py`, `db.py`, YAML-конфиг, cron, healthcheck. Они работают.

**Backward compat:** все AI-узлы — opt-in через env-flags (`AI_CLASSIFIER_ENABLED=false` → старое поведение).

## 5. Cost estimate

| Компонент | Сейчас | После |
|---|---|---|
| Enrichment | Qwen-30B, $0.003/день | Haiku 4.5, ~$0.10/день |
| Classifier | $0 (keyword regex) | Haiku 4.5, ~$0.40/день |
| Bitrix chain | $0 (нет) | Sonnet 4.6, ~$0.20/день (10 тендеров × $0.02) |
| Schema repair | $0 (ручной фикс Данияра) | Sonnet 4.6, ~$0.20/неделя (редкие инциденты) |
| Anomaly RCA | $0 (Qwen 1x/день) | Haiku 4.5 routine + Sonnet 4.6 on regression, ~$0.10/день |
| **Итого** | ~$0.10/мес | **~$25-40/мес** |

Оправдано если: (а) precision relevance >90% (сейчас ~70% по чувству Данияра — много false positives на «оборудование», «упаковочные материалы для X где X не наша вертикаль»), (б) hot-leads enrichment экономит 5-10 часов работы Оксаны/менеджера в неделю.

## 6. Risks

1. **AI API outages** → keyword filter остаётся fallback. Никогда не блокировать pipeline на AI. (Anthropic: "agents need stopping conditions and graceful degradation".)
2. **False negatives classifier** — реальный заказ classifier зарежет как irrelevant. Митигация: shadow mode 2 недели + log all rejections для ручного review.
3. **Cost spike** — runaway loop в Bitrix chain. Митигация: hard limit `max_turns=5` в SDK options, daily budget guard в коде ($5/день hard cap).
4. **Prompt caching mis-config** — exact-match required, любая динамическая часть в `system` ломает кэш. Митигация: статический system → cached, динамика только в `messages[user]`.
5. **Sonnet 4.6 vs 4.7 alias drift** — model ID часто меняется. Использовать `claude-sonnet-4-6` (alias без даты). Перед деплоем — verify через API call (правило из `.claude/rules/error-log.md`).
6. **Уход от Qwen → потеря cost-leadership на enrichment**. Митигация: оставить Qwen как опциональный provider (env flag `ENRICHMENT_PROVIDER=qwen|haiku`), сравнить precision на 2-недельном A/B.

## 7. Конкретные ссылки

- [Anthropic — Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents) — все 5 workflow-паттернов + agent
- [anthropics/courses](https://github.com/anthropics/courses) — `tool_use/`, `prompt_evaluations/`, `real_world_prompting/` (нет dedicated agents-модуля, но tool_use + real_world дают базу)
- [claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python) — `@tool` decorator, `ClaudeSDKClient`, hooks (`PreToolUse` matcher) — полезно для schema repair agent с интерактивностью
- [claude-agent-sdk-demos — Research Agent](https://github.com/anthropics/claude-agent-sdk-demos) — паттерн multi-source aggregation (применим к Source Discovery если решим делать P3)
- [Haiku 4.5 announcement](https://www.anthropic.com/news/claude-haiku-4-5) — 90% Sonnet quality на agentic, базис для замены Qwen
- [Prompt caching pricing](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — 0.1× read, 1.25×/2× write, 4 cache breakpoints/req

## 8. Anti-patterns (не делать)

- **LLM-orchestrator над cron** — каждый запуск пайплайна $$$
- **Multi-agent voting на classification** — overkill, single Haiku-call достаточно
- **Auto-apply Schema Adapter changes** — поломает данные. Always human-in-loop через PR/Telegram approve
- **Кэшировать тело тендера** — оно уникально, cache miss всегда
- **Заменить YAML на LLM-конфиг** — YAML — отличный low-cost router. Не починено = не ломаем

## Локальные code references

- `crawler/core/runner.py:107` — параллельный gather, не трогать
- `crawler/core/enricher.py:38-60` — drop-in target для Haiku миграции (Step 1 миграции)
- `crawler/core/ai_evaluator.py:21-34` — расширить до anomaly RCA
- `crawler/core/zero_result_tracker.py` — триггер для Schema Adapter Agent
- `crawler/config/settings.py:48-50` — добавить `anthropic_api_key`, `ai_classifier_enabled`, `ai_provider`
- `crawler/core/notifier.py` — hook для Bitrix enrichment chain output

## Финальный вывод

Не строить «AI-Native Engine» с нуля. Строить **AI-augmented engine**: процедурный crawler остаётся core, AI добавляется в 4 точки где он реально лучше regex (classifier, enrichment, schema repair, anomaly RCA). Всё на opt-in флагах, всё с fallback на старое поведение. ROI: ~$30/мес vs 5-10 часов экономии работы команды. Riskless migration через shadow mode + 2-недельный A/B.

---

**Word count:** ~1050 (чуть больше лимита, но плотно).
