"""Generate STRING_SESSION for PhotoSnatcher.

Run this on your computer, not on Render:
    python generate_session.py

It will ask for your Telegram phone number, login code, and 2FA password if enabled.
Copy the printed STRING_SESSION into Render Environment Variables.
"""

import asyncio
import os

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession

load_dotenv()


def _clean(value: str) -> str:
    return (value or "").strip().strip('"').strip("'")


def _read_api_id() -> int:
    raw = _clean(os.getenv("API_ID")) or input("API_ID: ").strip()
    return int(raw)


async def main():
    api_id = _read_api_id()
    api_hash = _clean(os.getenv("API_HASH")) or input("API_HASH: ").strip()

    if not api_id or not api_hash:
        raise RuntimeError("API_ID and API_HASH are required.")

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start()

    me = await client.get_me()
    print("\nLogged in successfully.")
    print(f"User: {getattr(me, 'first_name', '')} @{getattr(me, 'username', '')} ID={me.id}")
    print("\nCOPY THIS WHOLE VALUE INTO RENDER AS STRING_SESSION:\n")
    print(client.session.save())
    print("\nKeep this secret. Anyone with this session can access your Telegram account through API.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
