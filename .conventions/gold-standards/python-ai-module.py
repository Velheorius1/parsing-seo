"""Gold standard: Python AI module (OpenRouter call + Telegram report).

Based on: crawler/core/ai_evaluator.py
Pattern: daily-only guard -> compute stats -> OpenRouter AI -> Telegram send.
"""
# 1. Daily-only guard: /tmp marker file
_MARKER = "/tmp/last_{module_name}.txt"  # one marker per module
def _already_ran_today():  # check date string in marker
    pass
def _mark_done():  # write today's date to marker
    pass

# 2. OpenRouter call: async httpx, settings.openrouter_api_key
async def _call_openrouter(data):  # type: (dict) -> Optional[str]
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": "Bearer %s" % settings.openrouter_api_key},
            json={"model": settings.ai_relevance_model,
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 500, "temperature": 0.3},
        )
    # Strip Qwen3 <think>...</think> tags from response
    # Return None on any error (never raise)

# 3. Telegram send: escape markdown, disable_notification=True
async def _send_telegram(text):  # type: (str) -> bool
    text = text.replace("*", "").replace("_", "").replace("`", "")  # escape md
    bot_url = "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token
    # json={"chat_id": settings.telegram_alert_chat_id, "parse_mode": "Markdown"}

# 4. Main entry: guard -> compute -> AI -> format -> send/log
async def run_module(dry_run=False):  # type: (bool) -> None
    if _already_ran_today(): return
    stats = _compute_stats(...)            # pure computation
    analysis = await _call_openrouter(stats)  # AI recommendations
    message = _format_message(stats, analysis) # Markdown text
    if dry_run: logger.info("DRY RUN:\n%s", message)
    else: await _send_telegram(message)
    _mark_done()
