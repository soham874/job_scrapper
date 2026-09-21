"""Read-only analytics over what the borgs have found.

The tracker tab answers "where are my applications?". This one answers the
question underneath it — "is there anything to apply to?" — which is a
property of the companies being watched rather than of any one job.

Three things are on the page, and the first two are deliberately plain text
and bars rather than charts: how many tracked companies posted at all, which
ones posted most, and how the flow of jobs moved day by day. Only the last is
genuinely a trend, and only it gets a line chart.

Everything is measured over one window, chosen at the top and applied to the
whole page, so no two numbers on it are ever read off different slices. The
window is whole days including today, on the database's clock — see the
analytics section of common.db.repository for why the date is "first seen"
rather than "posted", which is the one caveat worth carrying while reading
any of this.

The chart is drawn in the browser from a JSON blob rendered into the page.
That keeps the company filter instant — picking a company is a redraw, not a
round trip — and costs nothing in payload, because the per-company series are
sparse and the whole window is a few thousand numbers at most. With
JavaScript off, the headline, the top five and the ATS table still render, and
the day-by-day figures are in the table under the chart rather than lost.
"""

import json
from datetime import date, datetime
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse

from common.bot.webui import authorized, document, e, render
from common.db.repository import (
    get_ats_job_stats,
    get_jobs_per_day_by_company,
    get_posting_summary,
    get_top_companies_by_jobs,
)
from common.logger import get_logger

logger = get_logger("bot.analytics")

router = APIRouter()

# Presets, not a free-form number. A range is a whole-table date scan, and
# ?days=100000 is a cheap way to make the page read every row job_info has.
RANGES = ((7, "7 days"), (30, "30 days"), (90, "90 days"), (180, "180 days"))
DEFAULT_DAYS = 30

_TOP_N = 5

# The chart's palette has eight slots and re-ordering it breaks colour-vision
# separation, so eight is also the cap on how many companies can be plotted at
# once. Past that the honest answer is fewer lines, not a ninth colour nobody
# can tell from the third.
MAX_SERIES = 8


def _days(value: Optional[int]) -> int:
    return value if value in {days for days, _ in RANGES} else DEFAULT_DAYS


def _fmt_day(value) -> str:
    if value is None:
        return "—"
    return value.strftime("%d %b") if hasattr(value, "strftime") else str(value)


def _iso(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _as_date(value):
    """datetime.datetime subclasses date, so isinstance cannot tell them apart
    and a stray timestamp would miss every key in the day index."""
    return value.date() if isinstance(value, datetime) else value


def _series(days: int, rows: list, end=None) -> dict:
    """Turn (day, company, jobs) rows into what the chart script reads.

    The day axis is built from the window rather than from the rows, so a
    quiet Sunday is a zero on the line instead of a gap the chart draws
    straight through — a line that skips its empty days reads as steady flow
    when the truth is a burst and a week of nothing.

    Per-company counts stay sparse — [day index, jobs] pairs — because most
    companies post on a handful of days in any window, and a dense array per
    company would be almost entirely zeroes.
    """
    # `end` is the window end the summary query read off the database, so the
    # axis the chart draws and the range the page prints are the same days. It
    # still yields to a row dated later than that, which only happens if the
    # two queries straddled midnight.
    end = _as_date(end) if hasattr(end, "toordinal") else date.today()
    days_by_row = [_as_date(row["day"]) for row in rows]
    for day in days_by_row:
        if hasattr(day, "toordinal"):
            end = max(end, day)
    axis = [date.fromordinal(end.toordinal() - offset) for offset in range(days - 1, -1, -1)]
    index = {day: position for position, day in enumerate(axis)}

    aggregate = [0] * len(axis)
    companies: dict = {}
    for row, day in zip(rows, days_by_row):
        position = index.get(day)
        if position is None:              # outside the axis; nothing to plot it on
            continue
        jobs = int(row["jobs"])
        aggregate[position] += jobs
        companies.setdefault(row["company"], []).append([position, jobs])

    ordered = sorted(
        ({"name": name, "total": sum(jobs for _, jobs in points), "points": points}
         for name, points in companies.items()),
        key=lambda series: (-series["total"], series["name"]),
    )
    return {"days": [_iso(day) for day in axis],
            "labels": [_fmt_day(day) for day in axis],
            "aggregate": aggregate,
            "companies": ordered,
            "max_series": MAX_SERIES}


def _ranges(active: int, token: Optional[str]) -> str:
    """The date range, as links rather than buttons.

    Every number on the page comes from the server, so changing the range is a
    navigation — and as a link it survives JavaScript being off, gets a real
    back button, and can be bookmarked at the range someone actually wants.
    """
    out = []
    for days, label in RANGES:
        params = {"days": days}
        if token:
            params["t"] = token
        on = " is-on" if days == active else ""
        current = ' aria-current="page"' if days == active else ""
        out.append(f'<a class="range{on}" href="?{e(urlencode(params))}"{current}>{e(label)}</a>')
    return "".join(out)


def _fill(width: float) -> str:
    """A bar's filled part, or nothing at all when there is nothing to show.

    The CSS floor that keeps a 1-of-90 bar visible would otherwise draw a stub
    for a zero, and a visible mark against "0 jobs" is the one thing the panel
    must not say.
    """
    return f'<span class="bar-fill" style="width:{width:.1f}%"></span>' if width > 0 else ""


def _meter(value: int, total: int) -> str:
    share = (value / total) if total else 0.0
    fill = (f'<span class="meter-fill" style="width:{share * 100:.1f}%"></span>'
            if share > 0 else "")
    return f'<span class="meter" role="presentation">{fill}</span>'


def _headline(summary: dict, days: int, series: dict) -> str:
    """The three figures the page leads with.

    A stat tile rather than a chart apiece: each of these is one number, and a
    one-bar bar chart is a worse way to read a number than the number is.
    """
    posting = summary["posting_companies"]
    enabled = summary["enabled_companies"]
    quiet = max(enabled - posting, 0)

    aggregate = series["aggregate"]
    busiest = max(range(len(aggregate)), key=lambda i: aggregate[i]) if aggregate else None
    if busiest is None or not aggregate[busiest]:
        busiest_value, busiest_label = "—", "nothing recorded yet"
    else:
        busiest_value = f"{aggregate[busiest]:,}"
        busiest_label = f"jobs on {e(series['labels'][busiest])}"

    return f"""
    <div class="tiles">
      <div class="tile tile-lead">
        <p class="t-label">Tracked companies posting</p>
        <p class="t-value">{posting:,} <span class="t-of">of {enabled:,}</span></p>
        {_meter(posting, enabled)}
        <p class="t-note">{quiet:,} posted nothing in the last {days} days</p>
      </div>
      <div class="tile">
        <p class="t-label">Jobs recorded</p>
        <p class="t-value">{summary['jobs']:,}</p>
        <p class="t-note">across every tracked company</p>
      </div>
      <div class="tile">
        <p class="t-label">Busiest day</p>
        <p class="t-value">{busiest_value}</p>
        <p class="t-note">{busiest_label}</p>
      </div>
    </div>"""


def _top_companies(rows: list, days: int) -> str:
    """The busiest companies, as bars against the busiest one.

    Bars are measured against the leader rather than the window's total: the
    question is which of these posted more than which, and a share-of-total
    scale squashes five companies into the left tenth of the card.
    """
    if not rows:
        return ('<p class="empty">No tracked company posted anything in the '
                f'last {days} days.</p>')
    top = max(row["jobs"] for row in rows) or 1
    out = []
    for row in rows:
        width = row["jobs"] / top * 100
        out.append(f"""
        <li class="bar-row">
          <span class="bar-name">{e(row['company'])}</span>
          <span class="bar-track">{_fill(width)}</span>
          <span class="bar-value">{row['jobs']:,}</span>
        </li>""")
    return f'<ol class="bars">{"".join(out)}</ol>'


def _ats_panel(stats: dict, days: int) -> str:
    """Jobs per ATS against the total, as a table with the share drawn in.

    A table rather than a pie: the question is "how much of the intake came
    through each integration", which is read off the numbers, and five slices
    of similar size is exactly what a pie is worst at. The bar is there to
    make the ordering scannable, not to carry the value — every figure is
    written out beside it.
    """
    total = stats["total_jobs"]
    rows = stats["rows"]
    if not rows:
        return '<p class="empty">No ATS is enabled yet.</p>'

    out = []
    for row in rows:
        share = row["share"] * 100
        companies = row["enabled_companies"]
        if row["tracked"]:
            note = f"{companies:,} enabled compan{'y' if companies == 1 else 'ies'}"
            name_cell = f'<span class="ats-name">{e(row["ats"])}</span>'
        else:
            note = "no enabled companies — history only"
            name_cell = f'<span class="ats-name is-off">{e(row["ats"])}</span>'
        out.append(f"""
        <tr>
          <th scope="row">{name_cell}<span class="ats-note">{e(note)}</span></th>
          <td class="ats-count">{row['jobs']:,}</td>
          <td class="ats-bar"><span class="bar-track">{_fill(share if row['jobs'] else 0)}</span></td>
          <td class="ats-share">{share:.1f}%</td>
        </tr>""")

    return f"""
    <table class="ats">
      <caption class="sr-only">Jobs recorded per ATS over the last {days} days</caption>
      <thead><tr>
        <th scope="col">ATS</th><th scope="col">Jobs</th>
        <th scope="col"><span class="sr-only">Share of all jobs, drawn as a bar</span></th>
        <th scope="col" class="ats-share">Share</th>
      </tr></thead>
      <tbody>{"".join(out)}</tbody>
      <tfoot><tr>
        <th scope="row">All ATS</th><td class="ats-count">{total:,}</td>
        <td></td><td class="ats-share">100%</td>
      </tr></tfoot>
    </table>"""


def _fallback_table(series: dict) -> str:
    """The chart's numbers, rendered server-side.

    This is the table view every chart owes its reader, and it doubles as what
    the page shows when the script never runs. The script replaces its body
    when companies are picked; until then the aggregate here is already right.
    """
    rows = "".join(
        f"<tr><th scope=\"row\">{e(label)}</th><td>{count:,}</td></tr>"
        for label, count in zip(series["labels"], series["aggregate"])
    )
    return ('<table class="numbers"><thead><tr><th scope="col">Day</th>'
            '<th scope="col">Jobs</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>')


def _options(series: dict) -> str:
    return "".join(
        f'<option value="{e(company["name"])}"></option>'
        for company in series["companies"]
    )


# The page's own CSS. Tokens, type, header, tabs and the sticky filter row
# come from common.bot.webui.
_CSS = """
  .sr-only {
    position:absolute; width:1px; height:1px; padding:0; margin:-1px;
    overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; border:0;
  }

  /* The range presets are the page's one filter, and they scope everything
     below them — cards, chart and ATS table read the same window. */
  .toolbar { gap:8px; flex-wrap:wrap; }
  .ranges { display:flex; gap:4px; padding:3px; flex:0 0 auto;
    background:var(--card); border:1px solid var(--line); border-radius:999px; }
  .range { display:flex; align-items:center; min-height:38px; padding:6px 14px;
    border-radius:999px; font-size:13px; color:var(--muted); white-space:nowrap; }
  .range:hover { color:var(--fg); text-decoration:none; }
  .range.is-on { background:var(--accent-soft); color:var(--accent); font-weight:600; }
  .range:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
  .range-label { font-size:12px; color:var(--muted); margin-left:2px; }

  .card {
    margin:16px 0 0; background:var(--card); border:1px solid var(--line);
    border-radius:var(--radius); box-shadow:var(--shadow); padding:16px 18px 18px;
  }
  .card h2 { font-size:15px; font-weight:600; margin:0; letter-spacing:-.005em; }
  .card .card-note { color:var(--muted); font-size:12px; margin:3px 0 14px; max-width:70ch; }
  .card-head { display:flex; align-items:baseline; justify-content:space-between; gap:12px; flex-wrap:wrap; }

  /* Stat tiles. One number each, proportional figures — tabular-nums makes a
     three-digit display number look gappy. */
  .tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(210px, 1fr)); gap:12px; margin:16px 0 0; }
  .tile { background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
    box-shadow:var(--shadow); padding:14px 16px 16px; }
  .t-label { margin:0; font-size:11px; font-weight:600; letter-spacing:.06em;
    text-transform:uppercase; color:var(--muted); }
  .t-value { margin:6px 0 0; font-size:34px; line-height:1.05; font-weight:600; letter-spacing:-.02em; }
  .tile-lead .t-value { font-size:40px; }
  .t-of { font-size:18px; font-weight:500; color:var(--muted); letter-spacing:0; }
  .t-note { margin:8px 0 0; font-size:12px; color:var(--muted); }

  .meter { display:block; height:6px; margin:12px 0 0; border-radius:999px;
    background:var(--seq-track); overflow:hidden; }
  .meter-fill { display:block; height:100%; border-radius:999px; background:var(--seq-fill); }

  /* Horizontal bars: name, track, value. The value is always written out, so
     the bar is ordering at a glance rather than the only way to read it. */
  .bars { list-style:none; margin:0; padding:0; display:grid; gap:10px; }
  .bar-row { display:grid; grid-template-columns:minmax(90px, 190px) minmax(90px, 1fr) auto;
    align-items:center; gap:12px; font-size:13.5px; }
  .bar-name { overflow-wrap:anywhere; font-weight:500; }
  .bar-track { display:block; width:100%; height:10px; border-radius:999px;
    background:var(--seq-track); overflow:hidden; }
  .bar-fill { display:block; height:100%; border-radius:999px; background:var(--seq-fill); min-width:2px; }
  .bar-value { font-variant-numeric:tabular-nums; font-size:13px; color:var(--muted); }

  table.ats { width:100%; border-collapse:separate; border-spacing:0; font-size:13.5px; }
  table.ats th, table.ats td { padding:9px 10px; border-bottom:1px solid var(--line); text-align:left;
    vertical-align:middle; }
  table.ats thead th { font-size:10.5px; font-weight:600; text-transform:uppercase;
    letter-spacing:.06em; color:var(--muted); }
  table.ats tbody th { font-weight:500; }
  table.ats tfoot th, table.ats tfoot td { border-bottom:none; color:var(--muted); font-size:12.5px; }
  table.ats tfoot th { font-weight:600; }
  .ats-name { display:block; }
  .ats-name.is-off { color:var(--muted); }
  .ats-note { display:block; font-size:11px; color:var(--faint); }
  .ats-count, .ats-share { font-variant-numeric:tabular-nums; white-space:nowrap; }
  .ats-share { text-align:right; color:var(--muted); }
  .ats-bar { width:38%; }
  table.ats td.ats-bar .bar-track { height:8px; }

  /* Chart -------------------------------------------------------------- */
  .picker { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:14px 0 0; }
  .picker input {
    font:inherit; font-size:13.5px; color:var(--fg); min-width:0; flex:1 1 200px; max-width:280px;
    background:var(--bg); border:1px solid var(--line); border-radius:999px;
    padding:8px 14px; min-height:40px; -webkit-appearance:none;
  }
  .picker input:focus-visible { outline:2px solid var(--accent); outline-offset:1px; border-color:transparent; }
  .picker button {
    font:inherit; font-size:13px; font-weight:500; color:var(--fg); cursor:pointer;
    background:var(--raised); border:1px solid var(--line); border-radius:999px;
    padding:8px 15px; min-height:40px;
  }
  .picker button:hover { border-color:var(--accent); }
  .picker button:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
  .picker-hint { font-size:12px; color:var(--muted); flex:1 1 100%; margin:0; }
  .picker-hint.is-warn { color:var(--warn); }

  /* The selected companies are the legend: swatch plus name plus total, so
     identity never rests on colour alone. Removing one is the same control. */
  .legend { display:flex; flex-wrap:wrap; gap:8px; margin:12px 0 0; padding:0; list-style:none; }
  .legend li {
    display:flex; align-items:center; gap:8px; font-size:12.5px;
    background:var(--raised); border:1px solid var(--line); border-radius:999px; padding:4px 6px 4px 10px;
  }
  .legend .key { width:14px; height:3px; border-radius:2px; flex:0 0 auto; }
  .legend .who { font-weight:500; overflow-wrap:anywhere; }
  .legend .tot { color:var(--muted); font-variant-numeric:tabular-nums; }
  .legend button {
    font:inherit; line-height:1; cursor:pointer; color:var(--muted); background:none;
    border:none; border-radius:999px; width:26px; height:26px; flex:0 0 auto;
  }
  .legend button:hover { color:var(--hot); background:var(--hot-bg); }
  .legend button:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }

  .chart-host { position:relative; margin:14px 0 0; }
  .chart-host svg { display:block; width:100%; height:auto; touch-action:pan-y; }
  .chart-host:focus-visible { outline:2px solid var(--accent); outline-offset:3px; border-radius:6px; }
  .gridline { stroke:var(--grid); stroke-width:1; }
  .axis-text { fill:var(--muted); font-size:10.5px; font-variant-numeric:tabular-nums; }
  .crosshair { stroke:var(--faint); stroke-width:1; }
  .series-line { fill:none; stroke-width:2; stroke-linejoin:round; stroke-linecap:round; }
  .series-area { stroke:none; opacity:.1; }
  .series-dot { stroke:var(--card); stroke-width:2; }
  .point-label { fill:var(--fg); font-size:11px; font-weight:600; }

  .tip {
    position:absolute; z-index:3; pointer-events:none; min-width:112px; max-width:220px;
    background:var(--card); border:1px solid var(--line); border-radius:10px;
    box-shadow:0 4px 14px rgba(0,0,0,.12); padding:8px 10px; font-size:12px;
  }
  .tip-day { color:var(--muted); font-size:11px; margin:0 0 5px; }
  .tip-row { display:flex; align-items:center; gap:7px; margin-top:3px; }
  .tip-row .key { width:12px; height:3px; border-radius:2px; flex:0 0 auto; }
  .tip-row .val { font-weight:600; font-variant-numeric:tabular-nums; }
  .tip-row .who { color:var(--muted); overflow-wrap:anywhere; }

  .numbers-wrap { margin:12px 0 0; }
  .numbers-wrap summary { cursor:pointer; font-size:12.5px; color:var(--muted); padding:4px 0; }
  .numbers-wrap summary:focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:4px; }
  .numbers-scroll { max-height:320px; overflow:auto; margin:8px 0 0;
    border:1px solid var(--line); border-radius:8px; }
  table.numbers { width:100%; border-collapse:separate; border-spacing:0; font-size:12.5px; }
  table.numbers th, table.numbers td { padding:6px 10px; border-bottom:1px solid var(--line);
    text-align:left; white-space:nowrap; }
  table.numbers td { font-variant-numeric:tabular-nums; }
  table.numbers thead th { position:sticky; top:0; background:var(--raised); z-index:1;
    font-size:10.5px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }
  table.numbers tbody tr:last-child th, table.numbers tbody tr:last-child td { border-bottom:none; }

  @media (max-width:640px) {
    .card { padding:14px 14px 16px; }
    .t-value { font-size:30px; }
    .tile-lead .t-value { font-size:34px; }
    .ats-bar { display:none; }
    .bar-row { grid-template-columns:minmax(70px, 1fr) minmax(60px, 1.6fr) auto; gap:9px; }
    .ranges { width:100%; }
    .range { flex:1 1 0; justify-content:center; padding:6px 8px; }
    .range-label { display:none; }
  }
"""


_BODY = """
<div class="toolbar">
  <div class="ranges" role="group" aria-label="Date range"><!--RANGES--></div>
  <span class="range-label">applied to every figure below</span>
</div>

<section class="card" aria-labelledby="posting-h">
  <div class="card-head">
    <h2 id="posting-h">Company posting activity</h2>
  </div>
  <p class="card-note"><!--POSTING_NOTE--></p>
  <!--TILES-->
</section>

<section class="card" aria-labelledby="top-h">
  <h2 id="top-h">Busiest companies</h2>
  <p class="card-note">The <!--TOP_N--> tracked companies that posted the most, measured against the busiest of them.</p>
  <!--TOP-->
</section>

<section class="card" aria-labelledby="trend-h">
  <div class="card-head">
    <h2 id="trend-h">Jobs per day</h2>
  </div>
  <p class="card-note" id="trend-note">Every tracked company added together. Add companies below to
    break the total into one line each.</p>

  <div class="picker">
    <label class="sr-only" for="company-input">Add a company to the chart</label>
    <input id="company-input" list="company-options" autocomplete="off" spellcheck="false"
           placeholder="Add a company…" aria-describedby="picker-hint">
    <datalist id="company-options"><!--OPTIONS--></datalist>
    <button type="button" id="company-add">Add</button>
    <button type="button" id="company-clear" hidden>Clear</button>
    <p class="picker-hint" id="picker-hint"><!--PICKER_HINT--></p>
  </div>
  <ul class="legend" id="legend" aria-label="Companies on the chart"></ul>

  <div class="chart-host" id="chart-host" tabindex="0" role="img"
       aria-label="Jobs recorded per day" aria-describedby="trend-note"></div>

  <details class="numbers-wrap" id="numbers-wrap">
    <summary>Show the numbers</summary>
    <div class="numbers-scroll" id="numbers"><!--NUMBERS--></div>
  </details>
</section>

<section class="card" aria-labelledby="ats-h">
  <h2 id="ats-h">Jobs by ATS</h2>
  <p class="card-note"><!--ATS_NOTE--></p>
  <!--ATS-->
</section>

<footer><!--FOOTNOTE--></footer>
"""


# The chart. Drawn with createElementNS rather than innerHTML throughout:
# company names come out of a Google Sheet by way of the database, and a name
# with a bracket in it should be a label, never markup.
_SCRIPT = """
(function () {
  var DATA = <!--DATA-->;
  var MAX = DATA.max_series;
  var NS = 'http://www.w3.org/2000/svg';
  var STORE = 'analytics.companies';

  var host = document.getElementById('chart-host');
  var legend = document.getElementById('legend');
  var input = document.getElementById('company-input');
  var addBtn = document.getElementById('company-add');
  var clearBtn = document.getElementById('company-clear');
  var hint = document.getElementById('picker-hint');
  var note = document.getElementById('trend-note');
  var numbers = document.getElementById('numbers');
  if (!host) return;

  var byName = {};
  DATA.companies.forEach(function (company) { byName[company.name.toLowerCase()] = company; });

  // Colour follows the company, not its position in the list: a slot is held
  // until that company is removed, so dropping one never repaints the rest.
  // Filled rather than left sparse: indexOf skips the holes in `new Array(n)`,
  // so a hole-y array reports itself as permanently full.
  function freshSlots() {
    var out = [];
    for (var i = 0; i < MAX; i++) out.push(null);
    return out;
  }
  var slots = freshSlots();
  var picked = [];
  var cursor = -1;         // crosshair day index, -1 for none
  var chart = null;        // the live drawing; every redraw replaces it
  var ready = false;       // suppresses redraws while the saved picks restore

  function remember() {
    try { window.localStorage.setItem(STORE, JSON.stringify(picked.map(function (p) { return p.name; }))); }
    catch (err) { /* private mode */ }
  }
  function recall() {
    try { return JSON.parse(window.localStorage.getItem(STORE)) || []; }
    catch (err) { return []; }
  }

  function colorOf(slot) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue('--series-' + (slot + 1)).trim() || '#888';
  }

  function add(name) {
    var typed = String(name || '').trim();
    if (!typed) { return say('', false); }
    var company = byName[typed.toLowerCase()];
    if (!company) { return say('No tracked company by that name posted in this window.', true); }
    for (var i = 0; i < picked.length; i++) {
      if (picked[i].name === company.name) { return say(company.name + ' is already on the chart.', false); }
    }
    var slot = slots.indexOf(null);
    if (slot === -1) {
      return say('The chart holds ' + MAX + ' companies. Remove one to add another.', true);
    }
    slots[slot] = company.name;
    picked.push({ name: company.name, slot: slot, series: company });
    say('', false);
    return render();
  }

  function remove(name) {
    picked = picked.filter(function (entry) {
      if (entry.name !== name) return true;
      slots[entry.slot] = null;
      return false;
    });
    say('', false);
    render();
  }

  function say(message, warn) {
    hint.textContent = message || (picked.length
      ? 'Showing ' + picked.length + ' of ' + MAX + ' company lines.'
      : 'No company picked — the chart shows every tracked company added together.');
    hint.classList.toggle('is-warn', !!warn);
  }

  // -- element helpers ------------------------------------------------------
  function svg(name, attrs) {
    var node = document.createElementNS(NS, name);
    for (var key in attrs) { if (attrs.hasOwnProperty(key)) node.setAttribute(key, attrs[key]); }
    return node;
  }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  // Dense counts for one company, so every series is indexed the same way as
  // the day axis and a missing day plots as the zero it is.
  function densify(company) {
    var out = new Array(DATA.days.length);
    for (var i = 0; i < out.length; i++) out[i] = 0;
    company.points.forEach(function (point) { out[point[0]] += point[1]; });
    return out;
  }

  function visible() {
    if (!picked.length) {
      return [{ name: 'All tracked companies', values: DATA.aggregate, color: colorOf(0), lead: true }];
    }
    return picked.map(function (entry) {
      return { name: entry.name, values: densify(entry.series), color: colorOf(entry.slot), lead: false };
    });
  }

  function niceMax(value) {
    if (value <= 4) return Math.max(value, 1);
    var power = Math.pow(10, Math.floor(Math.log(value) / Math.LN10));
    var steps = [1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10];
    for (var i = 0; i < steps.length; i++) {
      if (steps[i] * power >= value) return steps[i] * power;
    }
    return 10 * power;
  }

  function ticksFor(top) {
    var count = top <= 4 ? top : 0;
    var candidates = [4, 5, 3, 2];
    for (var c = 0; !count && c < candidates.length; c++) {
      if (top % candidates[c] === 0) count = candidates[c];
    }
    if (!count) count = 4;
    var out = [];
    for (var i = 0; i <= count; i++) {
      var value = Math.round(top * i / count);
      if (out.indexOf(value) === -1) out.push(value);
    }
    return out;
  }

  // -- the chart ------------------------------------------------------------
  function drawChart() {
    var series = visible();
    var days = DATA.days.length;
    var width = Math.max(host.clientWidth || 640, 260);
    var narrow = width < 560;
    var padL = narrow ? 30 : 38, padR = 14, padT = 12, padB = 26;
    var height = narrow ? 210 : 280;
    var plotW = Math.max(width - padL - padR, 10);
    var plotH = Math.max(height - padT - padB, 10);

    var peak = 0;
    series.forEach(function (one) {
      one.values.forEach(function (value) { if (value > peak) peak = value; });
    });
    var top = niceMax(peak);
    var stepX = days > 1 ? plotW / (days - 1) : 0;
    var x = function (i) { return days > 1 ? padL + i * stepX : padL + plotW / 2; };
    var y = function (value) { return padT + plotH - (value / top) * plotH; };

    var root = svg('svg', {
      viewBox: '0 0 ' + width + ' ' + height,
      width: width, height: height, 'aria-hidden': 'true',
    });

    ticksFor(top).forEach(function (value) {
      var at = y(value);
      root.appendChild(svg('line', {
        class: 'gridline', x1: padL, x2: padL + plotW, y1: at, y2: at,
      }));
      var label = svg('text', {
        class: 'axis-text', x: padL - 6, y: at + 3.5, 'text-anchor': 'end',
      });
      label.textContent = value.toLocaleString();
      root.appendChild(label);
    });

    // Roughly one label per 90px, so the dates thin out on a phone instead of
    // overprinting each other.
    var every = Math.max(1, Math.ceil(days / Math.max(2, Math.floor(plotW / 90))));
    var marks = [];
    for (var i = 0; i < days; i += every) marks.push(i);
    // The window's last day always gets a label — it is half of what the
    // chart claims to cover — unless it would print on top of its neighbour.
    if (marks[marks.length - 1] !== days - 1) {
      if (days - 1 - marks[marks.length - 1] < every / 2) marks.pop();
      marks.push(days - 1);
    }
    marks.forEach(function (i) {
      var anchor = i === 0 ? 'start' : (i === days - 1 ? 'end' : 'middle');
      var at = i === 0 ? padL - 4 : (i === days - 1 ? padL + plotW + 4 : x(i));
      var tick = svg('text', { class: 'axis-text', x: at, y: height - 8, 'text-anchor': anchor });
      tick.textContent = DATA.labels[i];
      root.appendChild(tick);
    });

    function path(values) {
      var out = '';
      for (var i = 0; i < values.length; i++) {
        out += (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(values[i]).toFixed(1);
      }
      return out;
    }

    series.forEach(function (one) {
      // A wash under the line only reads when there is one line; under eight
      // it is eight overlapping translucent blocks and the lines vanish.
      if (one.lead && days > 1) {
        root.appendChild(svg('path', {
          class: 'series-area', fill: one.color,
          d: path(one.values) + 'L' + x(days - 1).toFixed(1) + ' ' + y(0).toFixed(1) +
             'L' + x(0).toFixed(1) + ' ' + y(0).toFixed(1) + 'Z',
        }));
      }
      root.appendChild(svg('path', { class: 'series-line', stroke: one.color, d: path(one.values) }));
      // An end dot gives a one-day window something to show, and anchors the
      // eye at the right edge where the newest number is.
      root.appendChild(svg('circle', {
        class: 'series-dot', fill: one.color, r: 4,
        cx: x(days - 1), cy: y(one.values[days - 1] || 0),
      }));
    });

    // The one direct label the chart carries: the latest value on a single
    // line. With several series the labels would land on top of each other,
    // so identity moves to the legend and the values to the tooltip and table.
    if (series.length === 1 && days) {
      var last = series[0].values[days - 1] || 0;
      var rising = days < 2 || last >= series[0].values[days - 2];
      var mark = svg('text', {
        class: 'point-label', x: x(days - 1) - 7, 'text-anchor': 'end',
        y: rising ? Math.max(y(last) - 11, padT + 11)
                  : Math.min(y(last) + 17, padT + plotH - 3),
      });
      mark.textContent = last.toLocaleString();
      root.appendChild(mark);
    }

    var hair = svg('line', { class: 'crosshair', y1: padT, y2: padT + plotH, x1: 0, x2: 0 });
    hair.style.display = 'none';
    root.appendChild(hair);

    var hits = svg('rect', {
      x: padL - stepX / 2, y: padT, width: plotW + stepX, height: plotH, fill: 'transparent',
    });
    root.appendChild(hits);

    host.textContent = '';
    host.appendChild(root);

    var tip = el('div', 'tip');
    tip.hidden = true;
    host.appendChild(tip);

    function nearest(clientX) {
      var box = root.getBoundingClientRect();
      var scale = box.width / width;
      var local = (clientX - box.left) / (scale || 1);
      var index = stepX ? Math.round((local - padL) / stepX) : 0;
      return Math.max(0, Math.min(days - 1, index));
    }

    function show(index) {
      cursor = index;
      hair.style.display = '';
      hair.setAttribute('x1', x(index));
      hair.setAttribute('x2', x(index));

      tip.textContent = '';
      tip.appendChild(el('p', 'tip-day', DATA.labels[index]));
      series.forEach(function (one) {
        var row = el('div', 'tip-row');
        var key = el('span', 'key');
        key.style.background = one.color;
        row.appendChild(key);
        row.appendChild(el('span', 'val', (one.values[index] || 0).toLocaleString()));
        row.appendChild(el('span', 'who', one.name));
        tip.appendChild(row);
      });
      tip.hidden = false;

      var box = root.getBoundingClientRect();
      var scale = (box.width / width) || 1;
      var at = x(index) * scale;
      var half = tip.offsetWidth / 2;
      tip.style.left = Math.max(0, Math.min(box.width - tip.offsetWidth, at - half)) + 'px';
      tip.style.top = '4px';
    }

    function hide() {
      cursor = -1;
      hair.style.display = 'none';
      tip.hidden = true;
    }

    hits.addEventListener('pointermove', function (event) { show(nearest(event.clientX)); });
    hits.addEventListener('pointerdown', function (event) { show(nearest(event.clientX)); });
    hits.addEventListener('pointerleave', hide);
    host.addEventListener('blur', hide);

    if (cursor > -1 && cursor < days) show(cursor);
    host.setAttribute('aria-label', describe(series, days));
    return { days: days, show: show, hide: hide };
  }

  function describe(series, days) {
    var total = 0;
    series.forEach(function (one) {
      one.values.forEach(function (value) { total += value; });
    });
    var what = picked.length
      ? picked.length + ' companies'
      : 'every tracked company';
    return 'Jobs per day over ' + days + ' days for ' + what +
           ', ' + total.toLocaleString() + ' in total. The table below has the daily figures.';
  }

  // -- legend & table -------------------------------------------------------
  function drawLegend() {
    legend.textContent = '';
    clearBtn.hidden = !picked.length;
    // One line is named by the card's own title; a legend box would only
    // repeat it. Two or more always get one.
    if (!picked.length) { legend.hidden = true; return; }
    legend.hidden = false;
    picked.forEach(function (entry) {
      var item = document.createElement('li');
      var key = el('span', 'key');
      key.style.background = colorOf(entry.slot);
      item.appendChild(key);
      item.appendChild(el('span', 'who', entry.name));
      item.appendChild(el('span', 'tot', entry.series.total.toLocaleString()));
      var drop = el('button', null, '×');
      drop.type = 'button';
      drop.setAttribute('aria-label', 'Remove ' + entry.name + ' from the chart');
      drop.addEventListener('click', function () { remove(entry.name); });
      item.appendChild(drop);
      legend.appendChild(item);
    });
  }

  function drawTable(series) {
    var table = el('table', 'numbers');
    var head = document.createElement('thead');
    var headRow = document.createElement('tr');
    headRow.appendChild(cell('th', 'Day', 'col'));
    series.forEach(function (one) { headRow.appendChild(cell('th', one.name, 'col')); });
    head.appendChild(headRow);
    table.appendChild(head);

    var body = document.createElement('tbody');
    DATA.labels.forEach(function (label, i) {
      var row = document.createElement('tr');
      row.appendChild(cell('th', label, 'row'));
      series.forEach(function (one) { row.appendChild(cell('td', (one.values[i] || 0).toLocaleString())); });
      body.appendChild(row);
    });
    table.appendChild(body);

    numbers.textContent = '';
    numbers.appendChild(table);
  }

  function cell(tag, text, scope) {
    var node = el(tag, null, text);
    if (scope) node.setAttribute('scope', scope);
    return node;
  }

  function render() {
    if (!ready) return;
    drawLegend();
    var series = visible();
    chart = drawChart();
    drawTable(series);
    note.textContent = picked.length
      ? 'One line per company picked below. Days a company posted nothing are zeroes, not gaps.'
      : 'Every tracked company added together. Add companies below to break the total into one line each.';
    say('', false);
    remember();
  }

  // -- wiring ---------------------------------------------------------------
  addBtn.addEventListener('click', function () {
    add(input.value);
    input.value = '';
    input.focus();
  });
  input.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') { event.preventDefault(); addBtn.click(); }
  });
  // Picking from the datalist fires input rather than change in some browsers;
  // an exact hit on a known name is unambiguous either way.
  input.addEventListener('change', function () {
    if (byName[input.value.trim().toLowerCase()]) addBtn.click();
  });
  clearBtn.addEventListener('click', function () {
    picked = [];
    slots = freshSlots();
    render();
    input.focus();
  });

  // Keyboard reads the same values hover does — arrow along the days, Escape
  // to drop the readout.
  host.addEventListener('keydown', function (event) {
    var days = DATA.days.length;
    if (!days) return;
    var next = null;
    if (event.key === 'ArrowRight') next = cursor < 0 ? 0 : Math.min(days - 1, cursor + 1);
    else if (event.key === 'ArrowLeft') next = cursor < 0 ? days - 1 : Math.max(0, cursor - 1);
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = days - 1;
    else if (event.key === 'Escape') { if (chart) chart.hide(); return; }
    else return;
    event.preventDefault();
    cursor = next;
    if (chart) chart.show(next);
  });

  // The chart is sized from its container, so it has to be redrawn when the
  // container changes — a phone rotating, or a desktop window dragged narrow.
  var pending = null;
  function onResize() {
    if (pending) window.clearTimeout(pending);
    pending = window.setTimeout(function () { chart = drawChart(); }, 120);
  }
  if (window.ResizeObserver) { new window.ResizeObserver(onResize).observe(host); }
  else { window.addEventListener('resize', onResize); }

  recall().forEach(function (name) {
    // A company that posted nothing in this window has no line to draw, so a
    // remembered pick silently drops rather than showing an empty series.
    if (byName[String(name).toLowerCase()] && picked.length < MAX) add(name);
  });
  ready = true;
  render();

  // A theme flip repaints the tokens but not an SVG that is already drawn.
  var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
  if (dark && dark.addEventListener) {
    dark.addEventListener('change', function () { render(); });
  }
})();
"""


def _payload(series: dict) -> str:
    """The chart's data, safe to drop between <script> tags.

    json.dumps alone is not: a company name is sheet input, and one containing
    `</script>` would end the block early and put the rest of the payload on
    the page as markup. Escaping the three characters that can start a tag or
    a comment costs nothing and closes that off — the values decode unchanged,
    because \\u003c and `<` are the same string to a JSON parser.
    """
    raw = json.dumps(series, separators=(",", ":"))
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


@router.get("/analytics", response_class=HTMLResponse)
def analytics(t: Optional[str] = Query(default=None),
              days: Optional[int] = Query(default=None)):
    """The whole page, for one window."""
    if not authorized(t):
        return HTMLResponse("Not found", status_code=404)

    window = _days(days)
    summary = get_posting_summary(window)
    top = get_top_companies_by_jobs(window, _TOP_N)
    series = _series(window, get_jobs_per_day_by_company(window),
                     end=summary["window_end"])
    ats = get_ats_job_stats(window)

    span = f"{_fmt_day(summary['window_start'])} – {_fmt_day(summary['window_end'])}"
    sub = (f"{summary['jobs']:,} jobs first seen across "
           f"{summary['enabled_companies']:,} tracked companies · {span}")

    posting_note = (
        f"<strong>{summary['posting_companies']:,} out of "
        f"{summary['enabled_companies']:,} tracked companies</strong> posted at least one job "
        f"in the last {window} days ({e(span)})."
    )
    ats_note = (
        f"Every job recorded in the last {window} days, split by the ATS its company is on. "
        "An enabled ATS that found nothing is a zero rather than a missing row."
    )
    footnote = (
        "Dates are when a job was first seen by a scrape, not when the employer posted it — "
        "no ATS here reports a posting date. A company switched on mid-window contributes "
        "only from the point its borg started running."
    )

    body = render(
        _BODY,
        ranges=_ranges(window, t),
        posting_note=posting_note,
        tiles=_headline(summary, window, series),
        top_n=str(_TOP_N),
        top=_top_companies(top, window),
        options=_options(series),
        picker_hint="No company picked — the chart shows every tracked company added together.",
        numbers=_fallback_table(series),
        ats_note=ats_note,
        ats=_ats_panel(ats, window),
        footnote=footnote,
    )

    return HTMLResponse(document(
        title="Analytics",
        heading="Analytics",
        sub=sub,
        active="/analytics",
        token=t,
        css=_CSS,
        body=body,
        script=render(_SCRIPT, data=_payload(series)),
    ))


@router.get("/analytics/data")
def analytics_data(t: Optional[str] = Query(default=None),
                   days: Optional[int] = Query(default=None)):
    """The same figures as JSON, for anything that would rather not scrape HTML."""
    if not authorized(t):
        return JSONResponse({"error": "not found"}, status_code=404)

    window = _days(days)
    summary = get_posting_summary(window)
    rows = get_jobs_per_day_by_company(window)
    return JSONResponse({
        "days": window,
        "window_start": _iso(summary["window_start"]) if summary["window_start"] else None,
        "window_end": _iso(summary["window_end"]) if summary["window_end"] else None,
        "companies": {
            "enabled": summary["enabled_companies"],
            "posting": summary["posting_companies"],
            "top": get_top_companies_by_jobs(window, _TOP_N),
        },
        "jobs": summary["jobs"],
        "per_day": [{"day": _iso(row["day"]), "company": row["company"], "jobs": row["jobs"]}
                    for row in rows],
        "ats": get_ats_job_stats(window),
    })
