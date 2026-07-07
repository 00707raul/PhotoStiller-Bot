import os
import sys
import requests

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BASE_URL = os.getenv("BASE_URL", "").strip().rstrip("/")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

if not BOT_TOKEN:
    print("BOT_TOKEN is missing.")
    sys.exit(1)

if not BASE_URL:
    print("BASE_URL is missing. Example: https://photostiller-bot.onrender.com")
    sys.exit(1)

if not WEBHOOK_SECRET:
    print("WEBHOOK_SECRET is missing.")
    sys.exit(1)

webhook_url = f"{BASE_URL}/webhook/{WEBHOOK_SECRET}"
api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"

response = requests.post(
    api_url,
    json={"url": webhook_url, "drop_pending_updates": True},
    timeout=20,
)

print(response.status_code)
print(response.text)
