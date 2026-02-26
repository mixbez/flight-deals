# ✈️ Flight Deal Finder

Finds cheap one-way flights from Budapest (or any city) using the Aviasales/Travelpayouts API and sends Telegram notifications.
Provided by [Misha](https://aboutmisha.com/) 
## Setup

### 1. Get Aviasales API token

1. Register at [Travelpayouts](https://www.travelpayouts.com/)
2. Join the Aviasales program
3. Go to **Profile → API token** and copy your token

### 2. Create a Telegram bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram
2. Send `/newbot`, follow the prompts
3. Copy the **bot token** (e.g. `123456:ABC-DEF...`)
4. Start a chat with your new bot (send `/start`)
5. Get your **chat ID**: open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser after sending a message to the bot — look for `"chat":{"id":123456789}`

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure

Edit `config.json`:

```json
{
    "origin": "BUD",
    "days_ahead": 3,
    "base_price_eur": 20,
    "base_duration_minutes": 90,
    "price_increment_eur": 10,
    "increment_minutes": 30,
    "currency": "eur",
    "market": "hu",
    "limit": 100,
    "direct_only": false,
    "aviasales_token": "YOUR_TOKEN_HERE",
    "telegram_bot_token": "YOUR_BOT_TOKEN",
    "telegram_chat_id": "YOUR_CHAT_ID"
}
```

Or use environment variables (takes priority):

```bash
export AVIASALES_TOKEN=your_token
export TELEGRAM_BOT_TOKEN=your_bot_token
export TELEGRAM_CHAT_ID=your_chat_id
```

### 5. Run

```bash
python main.py
```

## Config parameters

| Parameter | Default | Description |
|---|---|---|
| `origin` | `BUD` | IATA code of departure airport |
| `days_ahead` | `3` | How many days ahead to search |
| `base_price_eur` | `20` | Max price for short flights (€) |
| `base_duration_minutes` | `90` | Duration threshold for base price (min) |
| `price_increment_eur` | `10` | Additional € per extra time block |
| `increment_minutes` | `30` | Size of extra time block (min) |
| `currency` | `eur` | Price currency |
| `market` | `hu` | Aviasales market (affects cached data) |
| `direct_only` | `false` | Only show non-stop flights |

## Price threshold logic

```
duration ≤ 1h30m  →  max €20
duration ≤ 2h00m  →  max €30
duration ≤ 2h30m  →  max €40
duration ≤ 3h00m  →  max €50
...
```

## GitHub Actions (run every hour for free)

Create `.github/workflows/check-flights.yml` in a repo:

```yaml
name: Check Flights
on:
  schedule:
    - cron: '0 * * * *'   # every hour
  workflow_dispatch:        # manual trigger

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - run: python main.py
        env:
          AVIASALES_TOKEN: ${{ secrets.AVIASALES_TOKEN }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
```

Then add your tokens as **Repository Secrets** in GitHub Settings → Secrets.
