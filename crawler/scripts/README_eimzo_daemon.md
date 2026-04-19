# Mac E-IMZO CAPIWS Daemon

Persistent Python daemon that runs on Данияр's Mac, talks to the E-IMZO local
CAPIWS WebSocket (`ws://127.0.0.1:64443`), and auto-refreshes JWT tokens for
all 4 Uzbek platforms every ~4 hours. Tokens are written directly to Supabase
`crawler_settings` via `session_store` — the VPS crawler picks them up on its
next cycle without any webhook.

## Prerequisites

1. **E-IMZO desktop app** installed on the Mac and running
   - Download: https://e-imzo.uz/ (Mac build)
   - Launch E-IMZO.app — it starts the local CAPIWS WebSocket on port 64443
2. **USB key** plugged in, recognised by E-IMZO
3. **Python 3.9+** on the Mac
4. **Project checkout** with this file at `crawler/scripts/README_eimzo_daemon.md`
5. **Supabase env vars** populated in `.env` at the project root:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALERT_CHAT_ID` (for alerts on 3 consecutive failures)

## Install

```bash
cd /path/to/parsing-seo
python3 -m pip install --user websockets httpx supabase pydantic-settings
```

(The daemon uses `websockets` for CAPIWS and `httpx` for platform HTTP calls.)

## Configure

Edit `.env` at the project root (or export in shell):

```
E_IMZO_KEY_TIN=123456789          # TIN of the key you want to sign with
E_IMZO_KEY_PIN=                   # OPTIONAL — leave blank to prompt at startup (recommended)
E_IMZO_PLATFORMS=ebirja           # OPTIONAL CSV; defaults to all four platforms
EIMZO_DAEMON_REFRESH_SECONDS=14400  # OPTIONAL — default 4h
```

Note: only `ebirja` has verified backend URLs right now. The other three
(`hayotbirja`, `xt-xarid`, `xarid-ebirja`) are skipped at startup with a
warning until the URLs are filled in at
`crawler/scripts/mac_eimzo_daemon.py:PLATFORM_BACKENDS` (use browser DevTools
Network tab to capture `/auth/challenge` and `/auth/login` endpoints).

## Run

### Recommended — inside tmux

```bash
tmux new -s eimzo
bash /path/to/parsing-seo/crawler/scripts/start_eimzo_daemon.sh
# type PIN when prompted, then press Ctrl+B then D to detach
```

The wrapper runs the daemon under `caffeinate -dis` so the Mac won't sleep
mid-cycle, auto-restarts with exponential backoff (10s → 30s → 90s → 270s,
capped at 300s), and rotates `~/.eimzo_daemon/daemon.log` when it grows past
10MB.

### One-shot (cron fallback)

```bash
python3 crawler/scripts/mac_eimzo_daemon.py --once
```

Performs a single refresh cycle and exits. Useful as an at-boot launchd job
or a one-off recovery after the daemon has been down.

### Dry run (no Supabase writes)

```bash
python3 crawler/scripts/mac_eimzo_daemon.py --once --dry-run
```

Fetches challenges, signs via CAPIWS, calls `/auth/login`, validates the
returned JWT, logs the length/alg/exp — but does NOT call `set_token`. Use
this when testing backend URL changes.

## Monitor

```bash
tail -f ~/.eimzo_daemon/daemon.log
```

Look for:

- `[EimzoDaemon] cycle ok — refreshed=['ebirja', ...] failures=0` — all good
- `[EimzoDaemon] cycle partial — …` — some platforms OK, others failed
- `[EimzoDaemon] cycle fail — …` — nothing refreshed this cycle
- `[EimzoDaemon] 3 consecutive refresh failures for <platform>` — TG alert fires

Healthcheck (`crawler/scripts/healthcheck.py`) also reads the
`eimzo_daemon_heartbeat` entry from `crawler_settings` to determine liveness.

## Stop

- **From the tmux session:** press `Ctrl+C`
- **From elsewhere on the Mac:**
  ```bash
  kill -TERM "$(cat /tmp/eimzo_daemon.pid)"
  ```

The daemon releases the flock at `/tmp/eimzo_daemon.lock` and removes
`/tmp/eimzo_daemon.pid` on clean shutdown.

## Double-launch protection

The daemon acquires `fcntl.flock(LOCK_EX | LOCK_NB)` on
`/tmp/eimzo_daemon.lock` at startup. If another instance already holds the
lock, it logs `Another daemon instance holds the lock, exiting.` and exits 0
(success). `start_eimzo_daemon.sh` also pre-checks via `pgrep` for clarity.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Another daemon instance holds the lock` | Already running | Expected — check `cat /tmp/eimzo_daemon.pid` |
| `No E-IMZO key found with TIN=…` | Wrong TIN or USB key not inserted | Verify TIN in E-IMZO UI; reinsert token |
| `CAPIWS pfx/load_key failed: …` | Wrong PIN or token locked | Retry with correct PIN; unlock token if needed |
| `Challenge request failed … ConnectError` | Platform backend is down or URL wrong | Check PLATFORM_BACKENDS in daemon source |
| `Skipping <platform> — backend URLs not verified` | URLs still `None` | Capture them via DevTools and edit `PLATFORM_BACKENDS` |
| `TG alert skipped — bot token/chat id missing` | `.env` vars absent | Populate TELEGRAM_BOT_TOKEN + TELEGRAM_ALERT_CHAT_ID |

## Security

- PIN is read via `getpass()` — never echoed to the terminal, never logged
- JWT values, PKCS7 signatures, response bodies, and response headers are
  NEVER logged (see `DECISIONS.md` RISK-3)
- Log output contains only platform ids, token lengths, JWT `alg`, expiry,
  and truncated error messages
- `grep eyJ ~/.eimzo_daemon/daemon.log` should return zero matches
