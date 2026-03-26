# Job Scrapper Monorepo

A Python monorepo that scrapes job postings from **Workday** and **Greenhouse** career pages, filtered for India-based roles posted in the last 24 hours.

## Structure

```
job-scrapper/
├── common/              # Shared utilities (config, logging)
│   ├── config.py        # CSV reader, paths, constants
│   └── logger.py        # Per-borg file + console logging
├── borgs/
│   ├── workday/          # Workday borg
│   │   ├── scraper.py    # WorkdayScraper (from POC notebook)
│   │   ├── cron.py       # Hourly cron loop + CSV writer
│   │   └── api.py        # FastAPI (health + trigger)
│   └── greenhouse/       # Greenhouse borg
│       ├── scraper.py    # GreenhouseScraper (from POC notebook)
│       ├── cron.py       # Hourly cron loop + CSV writer
│       └── api.py        # FastAPI (health + trigger)
├── run_scripts/          # Bash launchers
│   ├── run_workday.sh
│   ├── run_greenhouse.sh
│   └── run_all.sh
├── jobs/                 # Output CSVs (auto-created)
├── logs/                 # Log files (auto-created)
├── company_info.csv      # Input: company names + career page URLs
└── requirements.txt
```

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Running

### Individual borgs

```bash
bash run_scripts/run_workday.sh      # port 5001
bash run_scripts/run_greenhouse.sh   # port 5002
```

### All borgs together

```bash
bash run_scripts/run_all.sh
```

## API Endpoints

Each borg exposes:

| Method | Path       | Description                  |
|--------|------------|------------------------------|
| GET    | `/health`  | Health check                 |
| POST   | `/trigger` | Manually trigger a scrape run|

- **Workday**: `http://localhost:5001`
- **Greenhouse**: `http://localhost:5002`

## Cron

Each borg runs an automatic scrape every **1 hour** in a background thread. Companies are scraped **in parallel** (8 workers) for speed. Results are saved to `jobs/<borg>_<UTC timestamp>.csv`.

## Telegram Notifications (Optional)

Get push notifications for new jobs via a Telegram bot. After each cron run, if new jobs were found, a batch summary is sent to your Telegram chat.

### Setup

1. **Create a bot**: message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`, and follow the prompts. Copy the **bot token**.
2. **Get your chat ID**: message your new bot, then visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` — look for `"chat":{"id": ...}` in the response.
3. **Set env vars** in your `.env` file:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
   TELEGRAM_CHAT_ID=987654321
   ```

If these vars are not set, the notifier is silently skipped and the scraper runs as usual.

## Logs

Per-borg log files are written to the `logs/` directory:
- `logs/workday.log`
- `logs/greenhouse.log`
- `logs/common.config.log`
