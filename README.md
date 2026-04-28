# parsing-seo

Монорепо системы мониторинга тендеров Узбекистана (полиграфия, упаковка, печать).

**Источник истины:** VPS `46.62.155.190:/opt/parsing-seo/` (auto-pull from this repo).
Mac-копия `Second_Brain/Projects/parsing-seo/` удалена 2026-04-28 — не использовать.

## Структура

```
.
├── web/            ← Next.js frontend (deployed to Vercel from web/)
├── crawler/        ← Python crawler (running on VPS, cron-driven)
├── supabase/       ← Database migrations (`supabase/migrations/`)
├── scripts/        ← Shared shell scripts (run_crawl.sh, fetch_*)
├── docs/           ← Findings, plans, research
│   └── legacy/     ← Archived early-phase docs
└── main.md         ← Project context (sync to Second_Brain/Projects/parsing-seo/)
```

## Деплой

| Слой | Триггер | Куда |
|------|---------|------|
| `web/` | git push origin main | Vercel auto-deploy → parsing-seo.vercel.app |
| `crawler/` | git push origin main | VPS cron `*/5 * * * git pull --ff-only` |

## Разработка

Только через VPS (`ssh root@46.62.155.190 cd /opt/parsing-seo`) или git clone:

```bash
git clone git@github.com:Velheorius1/parsing-seo.git
```

## Команды

### Frontend (`web/`)
```bash
cd web
npm install
npm run dev          # localhost:3000
npm run build
```

### Crawler (`crawler/`, on VPS)
```bash
cd /opt/parsing-seo
.venv/bin/python -m crawler.main --dry-run
.venv/bin/python -m crawler.main --source <id>
.venv/bin/python -m crawler.scripts.healthcheck
```

## Стек

- **Frontend:** Next.js 14 (App Router), TypeScript, Tailwind CSS, Zustand
- **Crawler:** Python 3.10+, httpx, BeautifulSoup, Playwright (SPA), Telethon
- **Database:** Supabase (PostgreSQL) — project `oaoehczbycrabkprazts`
- **Hosting:** Vercel (web), Hetzner VPS (crawler)
- **AI:** Qwen3-30B-A3B via OpenRouter (relevance + enrichment)

## Конфигурация

- Frontend env: `web/.env.example` → `web/.env.local`
- Crawler env: `crawler/.env.example` → `/opt/parsing-seo/.env`
- Root `.env.example` — обзор всех переменных

Секреты НИКОГДА не коммитятся (см. `.gitignore`).

## Логи

- Crawler: `/var/log/parsing-seo*.log` на VPS
- Crawler internal: `crawler/logs/` (gitignored)
- Telegram alerts: `TELEGRAM_ALERT_CHAT_ID`

## Бэкап pre-restructure (2026-04-28)

Tag: `backup/pre-restructure-2026-04-28` (commit 951d119b).
Tar: `/root/backups/parsing-seo-migration-2026-04-28/vps-FULL.tar.gz` (chmod 600).
