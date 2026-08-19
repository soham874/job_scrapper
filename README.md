# Job Scrapper Monorepo

A Python monorepo that scrapes job postings from **Workday**, **Greenhouse**, **Oracle** and **Ashby** career pages, filtered for India-based roles posted in the last 24 hours.

## Structure

```
job-scrapper/
├── common/              # Shared utilities (config, logging)
│   ├── companies/       # Google Sheet fetch + company_info sync
│   ├── config.py        # Shared constants (cron interval)
│   └── logger.py        # Per-borg file + console logging
├── borgs/
│   ├── workday/          # Workday borg
│   │   ├── scraper.py    # WorkdayScraper (from POC notebook)
│   │   ├── cron.py       # Hourly cron loop + CSV writer
│   │   └── api.py        # FastAPI (health + trigger)
│   ├── greenhouse/       # Greenhouse borg
│   │   ├── scraper.py    # GreenhouseScraper (from POC notebook)
│   │   ├── cron.py       # Hourly cron loop + CSV writer
│   │   └── api.py        # FastAPI (health + trigger)
│   ├── oracle/           # Oracle Recruiting Cloud borg
│   │   ├── scraper.py    # OracleScraper
│   │   ├── cron.py       # Hourly cron loop
│   │   └── api.py        # FastAPI (health + trigger)
│   └── ashby/            # Ashby borg
│       ├── scraper.py    # AshbyScraper
│       ├── cron.py       # Hourly cron loop
│       └── api.py        # FastAPI (health + trigger)
├── run_scripts/          # Bash launchers
│   ├── run_workday.sh
│   ├── run_greenhouse.sh
│   ├── run_oracle.sh
│   ├── run_ashby.sh
│   └── run_all.sh
├── jobs/                 # Output CSVs (auto-created)
├── logs/                 # Log files (auto-created)
└── requirements.txt
```

The tracked-company list is not a file in the repo — it lives in the Google Sheet
described under [Tracked Companies](#tracked-companies-google-sheet).

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
bash run_scripts/run_oracle.sh       # port 5003
bash run_scripts/run_ashby.sh        # port 5004
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
- **Oracle**: `http://localhost:5003`
- **Ashby**: `http://localhost:5004`

## Cron

Each borg runs an automatic scrape every **1 hour** in a background thread. Companies are scraped **in parallel** (8 workers) for speed. Results are saved to `jobs/<borg>_<UTC timestamp>.csv`.

## Tracked Companies (Google Sheet)

Which companies get scraped is driven by a Google Sheet, not by migrations. Edit the
sheet and the change is picked up on the next borg run — no SQL, no restart.

### Columns

Only these columns are read; any other column in the sheet is ignored, so you can keep
notes, POCs and whatever else alongside them. Heading matching ignores case, spacing and
punctuation (`ATS Link`, `ats_link` and `Ats-Link` are all the same column).

| Column               | Required | Purpose                                              |
|----------------------|----------|------------------------------------------------------|
| `Company Name`       | yes      | Unique key. Renaming a company creates a new row.     |
| `ATS`                | yes      | `workday`, `greenhouse`, `oracle` or `ashby` — picks the borg. |
| `ATS Link`           | yes      | Career-site URL the scraper hits.                     |
| `Enable in tracker`  | yes      | `Yes` to track, `No` to stop. Blank counts as `No`.   |
| `Base Country`       | no       | Stored on the company row.                            |
| `Target Location`    | no       | Stored on the company row.                            |

For `ashby`, the `ATS Link` only has to identify the board. The board name on its own
(`confluent`), the public board URL (`https://jobs.ashbyhq.com/confluent`), a link to a
single posting, and the posting-API URL all resolve to the same board.

Switching a company to `No` — or deleting its row — only stops future scraping. The
company, its past jobs and its application history stay in the database, and flipping it
back to `Yes` resumes tracking against the same row.

### Setup

1. In the [Google Cloud console](https://console.cloud.google.com/), create a project,
   enable the **Google Sheets API**, and create a **service account**. Download its JSON
   key to the repo root as `service_account.json` (gitignored).
2. Open the JSON, copy the `client_email` value, and **share the sheet with that address
   as a Viewer**. Without this step the API returns a 404.
3. Set the vars in `.env`:
   ```
   COMPANY_SHEET_ID=<the long id in the sheet URL: /spreadsheets/d/<THIS>/edit>
   COMPANY_SHEET_TAB=Companies
   GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json
   ```
4. Verify the wiring:
   ```bash
   PYTHONPATH=. python3 run_scripts/sync_companies.py
   ```
   It prints how many rows were inserted, updated and disabled, and exits non-zero with
   the reason if the sheet cannot be read.

### When it syncs

Every borg syncs at the top of each run, before scraping. The borgs share a MySQL
named lock and a 5-minute freshness window, so only the first one through actually calls
the Sheets API. `run_all.sh` also syncs once at startup so credential problems surface
immediately rather than an hour later in a log file.

If the sheet is unreachable the sync logs an error and scraping continues with the
companies already in the database — an outage at Google never stops a run. A sync that
would produce zero companies is refused outright.

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

### Deciding on a job

Each notification carries a single **Apply** button. Pressing it records the
application, edits the message to show the decision, and kicks off resume
generation and the referral message.

There is no Reject button. A job you do not apply to is rejected by the
sweeper once it has gone `SWEEP_AGE_HOURS` without a decision — ignoring a
notification *is* the rejection. The sweeper runs inside the bot process and
touches only the database; it never edits or deletes the original Telegram
message, since by the time a job is swept the notification has already aged
out of the chat.

Both knobs are optional and default to the values below:

```
# Hours a job may sit undecided before the sweeper rejects it
SWEEP_AGE_HOURS=24
# How often the sweeper runs, in seconds
SWEEP_INTERVAL_SECONDS=21600
```

To clear a backlog immediately rather than waiting for the next tick:

```bash
python3 run_scripts/run_sweeper.py
```

## Logs

Per-borg log files are written to the `logs/` directory:
- `logs/workday.log`
- `logs/greenhouse.log`
- `logs/oracle.log`
- `logs/ashby.log`
- `logs/companies.sync.log`
