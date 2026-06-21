# Еженедельная рутина самоулучшения parsing-seo

**Что это:** scheduled Claude-агент, каждый понедельник проверяет, анализирует и улучшает краулер; ставит оценку 0–10; копит обучение из ошибок недели. Создаётся через `/schedule` (cron Mon 11:00 UTC). Этот файл — единый источник правды для промпта (агент в облаке не помнит сессию) и для ручного запуска.

**Окружение:** агенту нужны (а) SSH к VPS `root@46.62.155.190`, (б) Playwright MCP (браузер), (в) доступ к репозиторию `Velheorius1/parsing-seo`. Если запуск в среде без SSH/браузера — данные всё равно собираются VPS-кроном (`weekly_metrics --save`, см. ниже), а отчёт деградирует до детерминированной части без браузер-проверки ссылок (composite помечается «link=прошлая неделя»).

**Фоллбэк (всегда работает):** VPS-крон `Mon 06:30 UTC` пишет `docs/weekly/data/<iso_week>.json` (детерминированные метрики + sub-scores). Даже если Claude-рутина не отработает, данные свежие.

---

## Промпт рутины (self-contained — копия для scheduled-агента)

```
Ты — еженедельная рутина самоулучшения краулера parsing-seo. Сегодня понедельник.
Действуй автономно, в конце пришли отчёт Данияру в Telegram. Не отключай источники.

ШАГ 1 — Данные (детерминированные):
  ssh root@46.62.155.190 "cd /opt/parsing-seo && .venv/bin/python3 -m crawler.scripts.metrics_tracker --save >/dev/null 2>&1; .venv/bin/python3 -m crawler.scripts.weekly_metrics --save"
  Распарси JSON: this_week vs last_week (alerts, active/dead, p95, feedback), subscores_deterministic
  (precision/platform/recall/cost), weights, prune_candidates_report_only, link_integrity.sample_sources.

ШАГ 2 — Проверка ссылок (браузер):
  Для 6-10 свежих source_url из sample_sources (возьми из БД:
  ssh root@46.62.155.190 "cd /opt/parsing-seo && .venv/bin/python3 -c \"from crawler.core.db import _get_client; c=_get_client();
    [print(r['source'],r['source_url']) for s in ['UZEX Предквалификации','UZEX Э-магазин издательские услуги','XT-Xarid встречные аукционы','ETender UZEX']
     for r in (c.table('tenders').select('source,source_url').eq('source',s).order('collected_at',desc=True).limit(2).execute().data or [])]\"")
  Открой каждую в Playwright, screenshot, классифицируй valid_lot / wrong_card / no_data / homepage.
  link_integrity = % valid_lot, переведи в 0-10 (=10*доля). Это 25%-й sub-score.

ШАГ 3 — Composite 0-10:
  composite = 0.25*link + 0.25*precision + 0.20*platform + 0.15*recall + 0.15*cost.
  Сравни с прошлой неделей (последняя строка docs/weekly/LEARNINGS.md). Падение → объясни причину.

ШАГ 4 — /deep-think:
  Запусти /deep-think: "Как поднять охват/точность/стоимость parsing-seo на этой неделе?
  Данные: <вставь this_week, deltas, p95-регрессия, dead-источники, link-находки>.
  Дай 2-4 конкретных, проверяемых улучшения с оценкой эффекта."

ШАГ 5 — Отчёт + обучение:
  Запиши docs/weekly/<iso_week>-report.md: WoW-таблица, composite+Δ, топ-находки, идеи /deep-think,
  REPORT-ONLY список prune-кандидатов (показать, НЕ отключать). Допиши ОДНУ строку в
  docs/weekly/LEARNINGS.md (неделя | score(Δ) | главная находка | что применено | prune-кандидаты).
  Коммить эти доки.

ШАГ 6 — Telegram Данияру:
  ssh root@46.62.155.190 "cd /opt/parsing-seo && .venv/bin/python3 -c \"
    import os,httpx
    t=open('.env').read()  # не печатать токен
    tok=[l.split('=',1)[1].strip() for l in t.splitlines() if l.startswith('TELEGRAM_BOT_TOKEN=')][0]
    cid=[l.split('=',1)[1].strip() for l in t.splitlines() if l.startswith('TELEGRAM_ALERT_CHAT_ID=')][0]
    msg='''<сюда краткий отчёт: 📊 Score X/10 (Δ), 3 находки, prune N, идеи>'''
    httpx.post(f'https://api.telegram.org/bot{tok}/sendMessage',data={'chat_id':cid,'text':msg,'parse_mode':'Markdown'})\""
  (Альтернатива sender'а — добавить crawler/scripts/send_weekly.py.)

ШАГ 7 — Улучшения:
  Безопасные (добавить keyword, проверенный фикс шаблона, верифицированный браузером) — применяй сам:
  правка → commit → push в main (VPS подтянет за 5 мин) → верификация. Рискованные (отключение
  источника, смена модели, массовая правка) — ОПИШИ Данияру в отчёте, НЕ применяй.

ПРАВИЛА: источники — report-only (не отключать). Перед claim о цифрах — сверь с JSON. Метка
обратных тендеров и фиксы ссылок уже в проде (2026-06-21) — не дублируй.
```

---

## Ручной запуск
`/project parsing seo` → «прогони еженедельную рутину» (или вставь промпт выше). Подходит, когда облачный запуск без SSH/браузера.

## Рубрика оценки (фиксированная — для сравнимости недель)
| Sub-score | Вес | Источник |
|-----------|-----|----------|
| Link integrity | 25% | браузер-проверка % валидных ссылок (ШАГ 2) |
| Precision | 25% | 1 − FP/total из фидбека; при 0 фидбека — ПРОКСИ 8.0, помечать UNVERIFIED |
| Platform health | 20% | active/total источников |
| Recall | 15% | WoW стабильность алертов (якорь 6.8 из аудита охвата) |
| Cost & reliability | 15% | AI error% + p95 latency |
