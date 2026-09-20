"""Read-only web view of the application tracker.

Deliberately has no write path. Every action — status, reminders, contacts,
resumes — happens in Telegram, which authenticates the user for free by chat
id. A dashboard that could mutate would need its own login, session and CSRF
story; this one needs none of that because the worst a leaked URL can do is
show someone the job search.

Each row links to t.me/<bot>?start=job_<id>, so tapping it on a phone opens
that job's card in the chat with its buttons already attached. That is the
whole handoff — there is no id to copy or retype.

The page is one server-rendered table. Filtering, search and sorting run in
the browser over rows that are already there: the whole history is a few
hundred rows, so a round trip per keystroke would buy nothing, and with
JavaScript off the table still renders complete and readable.
"""

import html
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse

from common.applications import ACTIVE_STATUSES, STATUS_EMOJI, STATUSES, status_label
from common.bot.deeplinks import build_deep_link
from common.config import GHOST_AFTER_DAYS
from common.db.repository import get_active_applications, get_status_counts
from common.logger import get_logger

logger = get_logger("bot.dashboard")

router = APIRouter()

# Optional shared secret. Unset serves the page to anyone who finds the URL —
# fine behind a private network or tunnel, not on the public host the webhook
# needs. Set it and the page requires ?t=<token>.
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")

# Everything, not just what is live: the point of the page is the whole history
# at a glance. The bot's /active is the filtered view.
_ALL_STATUSES = tuple(STATUSES)
_ROW_LIMIT = 500


def _authorized(token: Optional[str]) -> bool:
    return not DASHBOARD_TOKEN or token == DASHBOARD_TOKEN


def _e(value) -> str:
    return html.escape(str(value)) if value not in (None, "") else ""


def _fmt_date(value) -> str:
    if not value:
        return "—"
    return value.strftime("%d %b %Y") if hasattr(value, "strftime") else str(value)


def _iso(value) -> str:
    """Sort key for a date cell. Empty sorts last, which is what a missing
    follow-up date should do."""
    return value.isoformat() if hasattr(value, "isoformat") else ""


def _fmt_age(days) -> str:
    if days is None:
        return ""
    days = int(days)
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days}d ago"


def _ats_job_id(app: dict) -> str:
    ats_job_id = app.get("ats_job_id")
    if ats_job_id in (None, ""):
        return str(app.get("job_id") or "—")
    return str(ats_job_id)


def _days_to_ghost(app: dict) -> Optional[int]:
    """Days of silence left before the nightly sweep closes this application,
    or None for one whose clock has already stopped."""
    idle = app.get("days_idle")
    return None if idle is None else GHOST_AFTER_DAYS - int(idle)


def _idle_cell(app: dict) -> str:
    """The idle clock, as a pill that gets louder as the ghost window closes.

    days_idle is NULL for a rejected or ghosted application — the repository
    stops the clock at a terminal status, because "quiet for 94 days" reads as
    an open question against an application that is already closed.
    """
    idle = app.get("days_idle")
    if idle is None:
        return ('<span class="idle idle-off" title="The clock stops once an application '
                'is rejected or ghosted">—</span>')

    idle = int(idle)
    left = GHOST_AFTER_DAYS - idle
    if left <= 0:
        tone, tip = "idle-hot", "Past the silent window — the next nightly sweep ghosts this"
    elif left <= 7:
        tone, tip = "idle-hot", f"Ghosted in {left} day(s) unless something moves"
    elif left <= 14:
        tone, tip = "idle-warn", f"Ghosted in {left} days unless something moves"
    else:
        tone, tip = "idle-ok", f"{left} days of silence left before this is ghosted"
    return f'<span class="idle {tone}" title="{_e(tip)}">{idle}d</span>'


def _row(app: dict) -> str:
    """One application.

    The company name is the deep link rather than a trailing "open" action: it
    is the widest, most obvious tap target in the row, and on a phone a link in
    the last column of a scrolling table is the one thing you cannot reach.

    Company, role and location share a cell. Eleven separate columns never fit
    a laptop, let alone a phone, and these three are read together anyway —
    one column with a hierarchy inside it beats three fighting for width.
    """
    status = app.get("status") or ""
    link = build_deep_link(app["job_id"])
    company = _e(app.get("company")) or "—"
    company_cell = f'<a class="tg" href="{_e(link)}">{company}</a>' if link else company

    posting = app.get("application_link")
    posting_cell = (
        f'<a class="ext" href="{_e(posting)}" target="_blank" rel="noopener">Posting ↗</a>'
        if posting else '<span class="muted">—</span>'
    )

    # A task with no date is what the picker leaves behind when a follow-up is
    # cleared, and rendering it under a dash reads as two missing values rather
    # than one thing still to do.
    task = _e(app.get("next_important_task"))
    next_date = app.get("next_important_date")
    if next_date:
        next_cell = _fmt_date(next_date)
        if task:
            next_cell += f'<span class="sub-line">{task}</span>'
    else:
        next_cell = task or '<span class="muted">—</span>'

    age = _fmt_age(app.get("days_since_applied"))
    applied_cell = _fmt_date(app.get("applied_on"))
    if age:
        applied_cell += f'<span class="sub-line">{age}</span>'

    # One lowercase blob per row is all the search box needs, and it keeps the
    # matching logic in the browser down to a substring test.
    haystack = " ".join(str(app.get(k) or "") for k in
                        ("company", "title", "location", "poc", "next_important_task"))
    haystack = f"{haystack} {_ats_job_id(app)} {STATUSES.get(status, status)}".lower()

    return f"""<tr data-status="{_e(status)}" data-search="{_e(haystack)}"
      data-company="{_e((app.get('company') or '').lower())}"
      data-applied="{_iso(app.get('applied_on'))}"
      data-next="{_iso(next_date)}"
      data-idle="{'' if app.get('days_idle') is None else int(app['days_idle'])}">
      <td class="c-status" data-l="Status"><span class="pill s-{_e(status)}">{_e(STATUS_EMOJI.get(status, ''))} {_e(STATUSES.get(status, status))}</span></td>
      <td class="c-app" data-l="Application">
        <span class="company">{company_cell}</span>
        <span class="role">{_e(app.get('title')) or '—'}</span>
        <span class="loc">{_e(app.get('location')) or '—'}</span>
      </td>
      <td class="c-id" data-l="Job ID"><span class="mono">{_e(_ats_job_id(app))}</span></td>
      <td class="c-applied" data-l="Applied">{applied_cell}</td>
      <td class="c-idle" data-l="Idle">{_idle_cell(app)}</td>
      <td class="c-next" data-l="Next">{next_cell}</td>
      <td class="c-poc" data-l="Contact">{_e(app.get('poc')) or '<span class="muted">—</span>'}</td>
      <td class="c-link" data-l="Posting">{posting_cell}</td>
    </tr>"""


def _chips(counts: dict) -> str:
    """The counts double as the filter.

    They were two controls in one place — stats you could only read, and no way
    to narrow the table. A count of eleven ghosts is most useful as a way to
    see those eleven.
    """
    total = sum(counts.values())
    live = sum(counts.get(s, 0) for s in ACTIVE_STATUSES)
    chips = [
        f'<button class="chip is-on" data-filter="all" aria-pressed="true">'
        f'<span class="n">{total}</span><span class="k">total</span></button>',
        f'<button class="chip" data-filter="live" aria-pressed="false">'
        f'<span class="n">{live}</span><span class="k">in play</span></button>',
    ]
    for status in STATUSES:
        if counts.get(status):
            chips.append(
                f'<button class="chip" data-filter="{status}" aria-pressed="false">'
                f'<span class="n">{counts[status]}</span>'
                f'<span class="k">{STATUS_EMOJI[status]} {STATUSES[status].lower()}</span></button>'
            )
    return "".join(chips)


_HEAD = """<table id="grid"><thead><tr>
  <th class="c-status" data-key="status" tabindex="0" role="button">Status</th>
  <th class="c-app" data-key="company" tabindex="0" role="button">Application</th>
  <th class="c-id">Job ID</th>
  <th class="c-applied" data-key="applied" data-default="desc" tabindex="0" role="button">Applied</th>
  <th class="c-idle" data-key="idle" data-default="desc" tabindex="0" role="button">Idle</th>
  <th class="c-next" data-key="next" tabindex="0" role="button">Next</th>
  <th class="c-poc">Contact</th>
  <th class="c-link">Posting</th>
</tr></thead><tbody>"""


# Placeholders are HTML comments rather than str.format fields: the CSS and the
# script below are full of braces, and doubling every one of them to survive
# .format() is how a stylesheet quietly acquires a syntax error.
_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>Application tracker</title>
<style>
  :root {
    --bg:#fbfbfa; --fg:#1a1a18; --muted:#6b6b66; --faint:#8d8d86;
    --line:#e4e4e0; --card:#ffffff; --raised:#f4f4f1;
    --accent:#2f6f4f; --accent-soft:#e6f1ea;
    --ok:#2f6f4f; --ok-bg:#e8f2ec; --warn:#8a6116; --warn-bg:#faefd9;
    --hot:#a33a2a; --hot-bg:#fbe9e5;
    --shadow:0 1px 2px rgba(0,0,0,.05); --radius:12px; --gutter:16px;
  }
  @supports (padding: max(0px)) {
    :root { --gutter: max(16px, env(safe-area-inset-left)); }
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#141417; --fg:#e9e9e5; --muted:#9a9a94; --faint:#7c7c76;
      --line:#2b2b31; --card:#1d1d22; --raised:#26262c;
      --accent:#7fc4a0; --accent-soft:#1e3a2c;
      --ok:#7fc4a0; --ok-bg:#1c3328; --warn:#e0b767; --warn-bg:#332912;
      --hot:#f0907c; --hot-bg:#3a1f1a;
      --shadow:none;
    }
  }
  * { box-sizing:border-box; }
  html { -webkit-text-size-adjust:100%; }
  body {
    margin:0; background:var(--bg); color:var(--fg);
    font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Inter,sans-serif;
    padding:18px var(--gutter) calc(48px + env(safe-area-inset-bottom));
    overflow-x:hidden;
  }
  .shell { max-width:1280px; margin:0 auto; }
  h1 { font-size:19px; margin:0; letter-spacing:-.01em; }
  .sub { color:var(--muted); font-size:12.5px; margin:3px 0 0; }

  /* The filters stay put; the title does not. On a long list the control you
     reach for is the one you want after scrolling, and a heading that stays
     pinned only costs height — which on a phone is the scarce thing. */
  .toolbar {
    position:sticky; top:0; z-index:5; background:var(--bg);
    display:flex; align-items:center; gap:12px;
    margin:12px calc(-1 * var(--gutter)) 0;
    padding:8px var(--gutter); border-bottom:1px solid var(--line);
  }
  .search { flex:0 0 280px; min-width:0; position:relative; }
  .search input {
    width:100%; font:inherit; font-size:14px; color:var(--fg);
    background:var(--card); border:1px solid var(--line); border-radius:999px;
    padding:9px 14px 9px 36px; min-height:40px; -webkit-appearance:none;
  }
  .search input::-webkit-search-cancel-button { cursor:pointer; }
  .search svg { position:absolute; left:13px; top:50%; transform:translateY(-50%);
    width:14px; height:14px; stroke:var(--faint); fill:none; stroke-width:2; pointer-events:none; }
  .search input:focus-visible { outline:2px solid var(--accent); outline-offset:1px; border-color:transparent; }

  /* One scrolling row rather than a wrapped block: the chips are a single
     control, and letting them stack pushes the table off a small screen. */
  .chips {
    flex:1 1 auto; display:flex; gap:8px; min-width:0;
    overflow-x:auto; scrollbar-width:none; -webkit-overflow-scrolling:touch;
    padding:2px 0;
  }
  .chips::-webkit-scrollbar { display:none; }
  .chip {
    flex:0 0 auto; display:flex; flex-direction:column; gap:1px; align-items:flex-start;
    background:var(--card); border:1px solid var(--line); border-radius:10px;
    padding:6px 11px; min-width:72px; min-height:44px; cursor:pointer;
    font:inherit; color:inherit; text-align:left; box-shadow:var(--shadow);
    transition:border-color .12s, background .12s;
  }
  .chip:hover { border-color:var(--accent); }
  .chip .n { font-size:16px; font-weight:600; line-height:1.2; font-variant-numeric:tabular-nums; }
  .chip .k { font-size:10.5px; color:var(--muted); text-transform:lowercase; white-space:nowrap; }
  .chip.is-on { background:var(--accent-soft); border-color:var(--accent); }
  .chip.is-on .k { color:var(--accent); }
  .chip:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }

  /* clip, not hidden: `hidden` would make this a scroll container, and the
     sticky table header inside it would then be pinned to a box that never
     scrolls — which parks it in the middle of the first rows. `clip` still
     keeps the corners rounded. */
  .wrap {
    margin:16px 0 0; background:var(--card); border:1px solid var(--line);
    border-radius:var(--radius); box-shadow:var(--shadow); overflow:clip;
  }
  /* Fixed layout, with the widths set on the header cells below. Auto layout
     sizes columns from their content, so one pathological token — a 36-char
     ATS id, a role title someone typed without spaces — widens the table past
     its container and pushes the last columns out of sight. Fixed widths plus
     break-anywhere text keep every column on screen whatever lands in it, and
     stop the columns jumping about as a filter changes which rows are shown. */
  table { table-layout:fixed; border-collapse:separate; border-spacing:0; width:100%; font-size:13.5px; }
  thead th {
    position:sticky; top:var(--toolbar-h, 0px); z-index:2; background:var(--raised);
    text-align:left; padding:9px 12px; font-size:10.5px; font-weight:600;
    text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
    border-bottom:1px solid var(--line); white-space:nowrap; user-select:none;
  }
  thead th[data-key] { cursor:pointer; }
  thead th[data-key]:hover { color:var(--fg); }
  thead th[data-key]::after { content:"↕"; opacity:.3; margin-left:5px; font-size:10px; }
  thead th.sort-asc::after { content:"↑"; opacity:1; color:var(--accent); }
  thead th.sort-desc::after { content:"↓"; opacity:1; color:var(--accent); }
  thead th:focus-visible { outline:2px solid var(--accent); outline-offset:-2px; }
  tbody td { padding:10px 12px; border-bottom:1px solid var(--line); vertical-align:top;
    overflow-wrap:anywhere; }
  tbody tr:last-child td { border-bottom:none; }
  tbody tr:hover td { background:var(--raised); }
  /* Dates and the pills are short and self-describing; an ATS id is neither,
     and some of them are 36-character uuids, so that column wraps. */
  .c-status, .c-applied, .c-idle, .c-link { white-space:nowrap; }
  th.c-status { width:104px; } th.c-id { width:96px; } th.c-applied { width:104px; }
  th.c-idle { width:64px; } th.c-next { width:150px; } th.c-poc { width:148px; }
  th.c-link { width:86px; }

  .company { display:block; font-weight:600; font-size:14px; }
  .role { display:block; }
  .loc, .sub-line { display:block; color:var(--muted); font-size:11.5px; }
  .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; color:var(--muted); }
  .muted { color:var(--muted); }
  a { color:var(--accent); text-decoration:none; }
  a:hover { text-decoration:underline; }
  a:focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:4px; }

  .pill { display:inline-block; font-size:11.5px; padding:3px 9px; border-radius:999px;
    border:1px solid var(--line); background:var(--raised); white-space:nowrap; }
  .s-applied  { background:var(--accent-soft); border-color:transparent; }
  .s-screening{ background:var(--warn-bg); border-color:transparent; }
  .s-interview{ background:var(--warn-bg); border-color:transparent; }
  .s-offer    { background:var(--ok-bg); border-color:transparent; color:var(--ok); font-weight:600; }
  .s-rejected { background:var(--hot-bg); border-color:transparent; }
  .s-ghosted  { background:var(--raised); color:var(--muted); }

  /* The idle clock reads as a temperature: the closer an application gets to
     the ghost window, the louder its pill. A stopped clock is a dash. */
  .idle { display:inline-block; min-width:38px; text-align:center; font-size:11.5px;
    font-variant-numeric:tabular-nums; padding:3px 8px; border-radius:999px; }
  .idle-ok { background:var(--ok-bg); color:var(--ok); }
  .idle-warn { background:var(--warn-bg); color:var(--warn); }
  .idle-hot { background:var(--hot-bg); color:var(--hot); font-weight:600; }
  .idle-off { color:var(--faint); }

  .empty { padding:44px 20px; text-align:center; color:var(--muted); }
  footer { color:var(--muted); font-size:12px; margin:14px 0 0; max-width:70ch; }
  [hidden] { display:none !important; }

  /* Laptop windows: padding, not content, is what pushes eight columns past
     the viewport here — tighten the gutters before dropping anything. */
  @media (min-width:900px) and (max-width:1120px) {
    table { font-size:12.5px; }
    thead th, tbody td { padding:8px 7px; }
    th.c-next, th.c-poc { width:128px; }
  }

  /* Phone and small tablet: a horizontally scrolling table hides the company
     link, which is the one thing worth tapping. Each row becomes a card —
     company and status on the top line, the rest as a labelled two-column
     grid underneath. These are the same cells, only laid out differently, so
     there is one copy of the markup rather than a table and a card list that
     drift apart. */
  @media (max-width:899px) {
    .toolbar { flex-wrap:wrap; gap:8px; }
    .search { flex:1 1 100%; order:-1; }
    table, tbody, tr, td { display:block; width:100%; }
    thead { display:none; }
    tbody tr {
      display:grid; grid-template-columns:1fr 1fr; gap:8px 12px;
      background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
      box-shadow:var(--shadow); padding:12px 14px; margin-bottom:10px;
    }
    tbody tr:hover td { background:none; }
    tbody td { border:none; padding:0; font-size:13px; line-height:1.4; }
    tbody td::before {
      content:attr(data-l); display:block; font-size:9.5px; letter-spacing:.07em;
      text-transform:uppercase; color:var(--faint); margin-bottom:1px;
    }
    .wrap { background:none; border:none; box-shadow:none; overflow:visible; }
    .c-app { grid-column:1; grid-row:1; }
    .c-status { grid-column:2; grid-row:1; justify-self:end; text-align:right; }
    .c-app::before, .c-status::before { display:none; }
    .company { font-size:16px; line-height:1.3; }
    .role { font-size:13.5px; }
    .c-link { align-self:end; }
    .idle { min-width:0; }
  }

  @media (max-width:360px) {
    tbody tr { padding:11px 12px; }
    .chip { min-width:66px; padding:6px 9px; }
  }

  @media (prefers-reduced-motion: reduce) { * { transition:none !important; } }
</style></head>
<body>
<div class="shell">
<header>
  <h1>Application tracker</h1>
  <p class="sub"><!--SUB--></p>
</header>

<div class="toolbar">
  <div class="chips" role="group" aria-label="Filter by status"><!--CHIPS--></div>
  <div class="search">
    <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"></circle><path d="M20 20l-3.5-3.5"></path></svg>
    <input id="q" type="search" autocomplete="off" spellcheck="false"
           placeholder="Search company, role, contact…" aria-label="Search applications">
  </div>
</div>

<div class="wrap"><!--TABLE--></div>
<p class="empty" id="noresults" hidden>Nothing matches that filter.</p>
<footer><!--NOTE--></footer>
</div>

<script>
(function () {
  var toolbar = document.querySelector('.toolbar');
  var grid = document.getElementById('grid');

  // The table header pins itself below the toolbar, so it has to know how tall
  // the toolbar currently is — which changes when the chips wrap onto their
  // own line on a narrow screen.
  function syncToolbarHeight() {
    document.documentElement.style.setProperty('--toolbar-h', toolbar.offsetHeight + 'px');
  }
  syncToolbarHeight();
  window.addEventListener('resize', syncToolbarHeight);

  if (!grid) return;
  var rows = Array.prototype.slice.call(grid.tBodies[0].rows);
  var chips = Array.prototype.slice.call(document.querySelectorAll('.chip'));
  var search = document.getElementById('q');
  var empty = document.getElementById('noresults');
  var live = <!--LIVE-->;
  var filter = 'all';

  function remember(key, value) {
    try { window.localStorage.setItem(key, value); } catch (e) { /* private mode */ }
  }
  function recall(key) {
    try { return window.localStorage.getItem(key); } catch (e) { return null; }
  }

  function matches(row) {
    if (filter === 'live') { if (live.indexOf(row.getAttribute('data-status')) === -1) return false; }
    else if (filter !== 'all' && row.getAttribute('data-status') !== filter) return false;
    var term = search.value.trim().toLowerCase();
    return !term || row.getAttribute('data-search').indexOf(term) !== -1;
  }

  function apply() {
    var shown = 0;
    rows.forEach(function (row) {
      var ok = matches(row);
      row.hidden = !ok;
      if (ok) shown++;
    });
    empty.hidden = shown > 0;
  }

  chips.forEach(function (chip) {
    chip.addEventListener('click', function () {
      filter = chip.getAttribute('data-filter');
      chips.forEach(function (other) {
        var on = other === chip;
        other.classList.toggle('is-on', on);
        other.setAttribute('aria-pressed', String(on));
      });
      remember('tracker.filter', filter);
      apply();
    });
  });

  search.addEventListener('input', apply);

  // Sorting is a DOM reorder of rows that are already loaded. Text keys
  // compare as strings, the idle count as a number, and a blank key always
  // sinks to the bottom whichever way the column points — an application with
  // no follow-up date is not "the earliest one".
  function sortBy(key, dir) {
    var numeric = key === 'idle';
    var sorted = rows.slice().sort(function (a, b) {
      var x = a.getAttribute('data-' + key) || '', y = b.getAttribute('data-' + key) || '';
      if (x === '' && y === '') return 0;
      // Returned before the direction is applied, so a blank sinks either way.
      if (x === '') return 1;
      if (y === '') return -1;
      if (numeric) { x = Number(x); y = Number(y); }
      var cmp = x < y ? -1 : (x > y ? 1 : 0);
      return dir === 'asc' ? cmp : -cmp;
    });
    var body = grid.tBodies[0];
    sorted.forEach(function (row) { body.appendChild(row); });
  }

  Array.prototype.slice.call(grid.querySelectorAll('th[data-key]')).forEach(function (th) {
    function run() {
      var dir = th.classList.contains('sort-asc') ? 'desc'
              : th.classList.contains('sort-desc') ? 'asc'
              : (th.getAttribute('data-default') || 'asc');
      Array.prototype.slice.call(grid.querySelectorAll('th')).forEach(function (other) {
        other.classList.remove('sort-asc', 'sort-desc');
      });
      th.classList.add(dir === 'asc' ? 'sort-asc' : 'sort-desc');
      sortBy(th.getAttribute('data-key'), dir);
    }
    th.addEventListener('click', run);
    th.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); run(); }
    });
  });

  var saved = recall('tracker.filter');
  if (saved && saved !== 'all') {
    var chip = chips.filter(function (c) { return c.getAttribute('data-filter') === saved; })[0];
    if (chip) chip.click();
  }
})();
</script>
</body></html>"""


def _render(page: str, **parts) -> str:
    for name, value in parts.items():
        page = page.replace(f"<!--{name.upper()}-->", value)
    return page


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(t: Optional[str] = Query(default=None)):
    """The tracker as one page."""
    if not _authorized(t):
        return HTMLResponse("Not found", status_code=404)

    apps = get_active_applications(_ALL_STATUSES, limit=_ROW_LIMIT, offset=0)
    counts = get_status_counts()

    if apps:
        table = _HEAD + "".join(_row(a) for a in apps) + "</tbody></table>"
    else:
        table = ('<div class="empty">No applications yet. Apply to a job from Telegram '
                 'and it will appear here.</div>')

    total = sum(counts.values())
    live = sum(counts.get(s, 0) for s in ACTIVE_STATUSES)
    sub = (f"{live} in play of {total} · silence past {GHOST_AFTER_DAYS} days is ghosted "
           f"automatically · as of {datetime.now().strftime('%d %b, %H:%M')}")
    if total > _ROW_LIMIT:
        sub += f" · showing the most recent {_ROW_LIMIT}"

    note = (
        "Deep links are disabled — the bot username could not be resolved, so company names are plain text."
        if apps and not build_deep_link(apps[0]["job_id"])
        else "Tap a company to open that application in Telegram, where you can change its status, "
             "set a reminder, record a contact, or re-cut the resume."
    )
    return HTMLResponse(_render(
        _PAGE,
        sub=html.escape(sub),
        chips=_chips(counts),
        table=table,
        live="[" + ", ".join(f'"{s}"' for s in ACTIVE_STATUSES) + "]",
        note=html.escape(note),
    ))


@router.get("/dashboard/data")
def dashboard_data(t: Optional[str] = Query(default=None)):
    """Same rows as JSON, for anything that would rather not scrape HTML."""
    if not _authorized(t):
        return JSONResponse({"error": "not found"}, status_code=404)
    apps = get_active_applications(_ALL_STATUSES, limit=_ROW_LIMIT, offset=0)
    for app in apps:
        app["deep_link"] = build_deep_link(app["job_id"])
        app["status_label"] = status_label(app.get("status"))
        # days_idle is None once a status is terminal, and so is this — the
        # clock that would count down to a ghosting is not running.
        app["days_to_ghost"] = _days_to_ghost(app)
    return JSONResponse({
        "count": len(apps),
        "ghost_after_days": GHOST_AFTER_DAYS,
        "applications": jsonable(apps),
    })


def jsonable(apps: list) -> list:
    """Dates and datetimes out of MySQL are not JSON-serialisable on their own."""
    out = []
    for app in apps:
        row = {}
        for key, value in app.items():
            row[key] = value.isoformat() if hasattr(value, "isoformat") else value
        out.append(row)
    return out
