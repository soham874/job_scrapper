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
bash run_scripts/run_self_json.sh       # port 5005
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
- **Self JSON**: `http://localhost:5005`

## Cron

Each borg runs an automatic scrape every **1 hour** in a background thread. Companies are scraped **in parallel** (8 workers) for speed. Results are saved to `jobs/<borg>_<UTC timestamp>.csv`.

## The `self_json` borg — scraping an org with no supported ATS

The other four borgs know one ATS each. This one knows nothing: it is handed a curl and a
mapping, and every org that has a JSON jobs endpoint can be tracked without writing code.

Set `ATS` to `self_json`, put the curl in `ATS Link`, and add one more column, `Job Spec`.

### `ATS Link` — the curl

`ATS Link` holds whatever a borg needs to reach a board: a slug for `ashby`, a tenant URL
for `workday`, a host for `oracle` (whose parser already accepts one still wrapped in a
`curl --location '...'` snippet). For `self_json` it holds the whole curl.

Open the careers site, find the request that returns the job list in devtools, right-click
→ **Copy as cURL**, and paste it in as-is. Do not tidy it up.

That is not a style preference. The borg replays the request exactly as pasted because
these endpoints fail *silently* when it doesn't:

- `careers.klm.com` resets the connection outright when the `sec-ch-ua` / `sec-fetch-*`
  headers are missing — no response at all, not a 403.
- `jobs.apple.com` answers `200` with an empty result set when a single key is dropped
  from the POST body, which is indistinguishable from "no jobs today".

The only thing ever changed between requests is the one pagination field the spec names.

Because a copied curl carries the full header set, these cells run past the 1024 bytes
`ats_link` originally allowed — V017 widens the column to `TEXT` for that reason.

**Prefer a broad curl.** Search the board with an empty query rather than a narrow one and
let `TITLE_INCLUDE_KEYWORDS`/`TITLE_EXCLUDE_KEYWORDS` do the filtering — Apple's own
relevance ranking returns RF fixture design and manufacturing roles for a query of
"senior software engineer".

### `Job Spec`

```jsonc
{
  "jobs_path": "res.searchResults",          // where the job array lives
  "fields": {
    "job_id":      "positionId",             // the dedupe key; must be a single scalar
    "title":       "postingTitle",
    "location":    ["locations[].name", "locations[].countryName"],
    "posted":      "postDateInGMT",
    "description": "jobSummary",
    "slug":        "transformedPostingTitle" // extra fields are allowed, for the URL
  },
  "posted_format": "iso8601",
  "link_template": "https://jobs.apple.com/en-in/details/{job_id}/{slug}",
  "location_filter": "india",
  "pagination": { "type": "page", "in": "json", "page_field": "page",
                  "start": 1, "max_pages": 10, "delay_seconds": 0.4 },
  "detail": {                                 // optional second request per job
    "url_template": "https://jobs.apple.com/api/v1/jobDetails/{job_id}",
    "fields": { "description": ["res.description", "res.responsibilities",
                                "res.minimumQualifications"] }
  }
}
```

Required: `jobs_path`, `posted_format`, `fields.{job_id,title,location,posted}`, a
description source, and exactly one link source. Anything missing is rejected by name and
the company is skipped — the borg never guesses, because a guess that goes wrong looks
exactly like a company with no new jobs.

**Path grammar** — no wildcards, no filters, no fallback chains:

| Form | Meaning |
|------|---------|
| `a.b.c` | dict keys |
| `a.0.b` | list index |
| `a[].b` | map the rest of the path over a list |
| `""` | the response root |
| `["a", "b"]` | **concatenate** a and b, in order |

Nested `[]` flattens: KLM stores descriptions as Portable Text blocks, so
`description[].children[].text` is what turns them back into prose. A list of paths is a
concatenation, *not* a fallback — a missing part contributes nothing rather than causing
the next path to be tried.

**`posted_format`** is declared, never sniffed: `iso8601`, `epoch_seconds`,
`epoch_millis`, `relative_text` ("Posted 3 Days Ago"), or a strptime string like
`%d %b %Y`. A bare number is a plausible reading of both epoch formats, which is why it
has to be stated.

**`location_filter`** is `"india"` (the shared keyword list, the default), `"any"`, or an
explicit list like `["netherlands", "amsterdam"]`.

**`pagination.type`** is `none`, `offset`, or `page`; `in` picks the JSON body or the query
string. Paging stops on an empty page, a page shorter than the first, or `max_pages`.
Don't reach for a total-count field as a stop condition — Apple reports
`totalRecords: 80` on every real page and `0` on the overrun page.

Some boards ignore paging entirely and return their whole result set every time — the
Emirates Group board does. Use `"type": "none"` for those. If you get it wrong the borg
notices that a page repeated the previous one, stops, and logs which field is being
ignored, rather than fetching the same jobs `max_pages` times.

**`detail`** is an optional GET per surviving job, reusing the list request's headers. It
is usually needed: a list endpoint often carries only a marketing blurb. Apple's
`jobSummary` scores **0–3** against a threshold of 20, while its detail endpoint's
`description` + `responsibilities` + `minimumQualifications` score **23–45**. If a spec
declares no description source at all, it is rejected outright — every job would score 0
and be discarded without a single error in the log.

### Adding a source

```bash
PYTHONPATH=. python3 run_scripts/new_self_json_source.py --curl-file acme.txt
```

Executes the curl, finds the candidate job arrays, lists every path on a sample job with a
preview of its value, and writes out a validated spec. The curl goes in `ATS Link` and the
spec in `Job Spec`. It also
opens the first generated apply link to check the template — no API here returns one, so
that URL is always hand-written and a broken Apply button is otherwise invisible until
someone taps it. This is the only place anything is auto-detected, and a human reads the
result before it reaches the sheet.

Then dry-run it before switching the row on:

```bash
PYTHONPATH=. python3 run_scripts/new_self_json_source.py --curl-file acme.txt --spec-file acme.json --dry-run
```

This prints the whole funnel — returned by API, date readable, posted in last 24h, title
match, location match, then the analyzer score per job — and writes nothing to the
database or Telegram. It scores against the whole board rather than just the last 24
hours, so a spec can be verified on a quiet day. Once the row is in the sheet,
`--company "Acme"` reads the curl and spec straight from the database instead.

### When a source breaks

The sheet is the source of truth, so a typo in a path is the likeliest failure — and both
APIs above fail with HTTP 200 while doing it. The borg therefore reports, over Telegram,
any company that cannot be scraped: a missing curl or spec, a spec that fails validation,
an endpoint that never answers, or a request that succeeds and returns **zero jobs before
any filter**. That last one is the signature of a stale curl; pinned client-hint versions
expire and request bodies gain required keys. The fix is to re-copy the curl from devtools
into the sheet. Alerts fire only when the set of broken sources *changes*, so a
permanently broken row does not nag every half hour.


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
| `ATS`                | yes      | `workday`, `greenhouse`, `oracle`, `ashby` or `self_json` — picks the borg. |
| `ATS Link`           | yes      | Whatever the borg needs to reach the board — a career-site URL for most, the full curl for `self_json`. |
| `Enable in tracker`  | yes      | `Yes` to track, `No` to stop. Blank counts as `No`.   |
| `Base Country`       | no       | Stored on the company row.                            |
| `Target Location`    | no       | Stored on the company row.                            |
| `Job Spec`           | self_json | JSON saying where the jobs are and what each field maps to. Only read when `ATS` is `self_json`. |

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
