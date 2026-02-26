"""One-time Telethon authentication script.

Run this interactively to create a session file:
    python3 crawler/auth_telegram.py

It will ask for your phone number and the code from Telegram.
After that the session file is saved and the TelegramAdapter can use it.
"""

import asyncio
import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crawler.config.settings import settings  # noqa: E402


async def main() -> None:
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        print(
            "ERROR: Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first."
        )
        sys.exit(1)

    from telethon import TelegramClient

    client = TelegramClient(
        settings.telegram_session,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    await client.start()
    me = await client.get_me()
    print("Authenticated as: %s (id=%d)" % (me.first_name, me.id))
    print("Session saved to: %s.session" % settings.telegram_session)
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
