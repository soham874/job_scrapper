"""Read-only web view of the application tracker.

Deliberately has no write path. Every action — status, reminders, contacts,
resumes — happens in Telegram, which authenticates the user for free by chat
id. A dashboard that could mutate would need its own login, session and CSRF
story; this one needs none of that because the worst a leaked URL can do is
show someone the job search.

Each row links to t.me/<bot>?start=job_<id>, so tapping it on a phone opens
that job's card in the chat with its buttons already attached. That is the
whole handoff — there is no id to copy or retype.
"""

import html
import os
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse

from common.applications import ACTIVE_STATUSES, STATUS_EMOJI, STATUSES, status_label
from common.bot.deeplinks import build_deep_link
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


def _ats_job_id(app: dict) -> str:
    ats_job_id = app.get("ats_job_id")
    if ats_job_id in (None, ""):
        return str(app.get("job_id") or "—")
    return str(ats_job_id)


def _row(app: dict) -> str:
    """One application.

    The company name is the deep link rather than a trailing "open" action:
    it is the widest, most obvious tap target in the row, and on a phone a
    link in the last column of a scrolling table is the one thing you cannot
    reach.
    """
    link = build_deep_link(app["job_id"])
    company = _e(app.get("company"))
    company_cell = (
        f'<a class="tg" href="{_e(link)}">{company}</a>' if link else company
    )
    posting = app.get("application_link")
    posting_cell = (
        f'<a href="{_e(posting)}" target="_blank" rel="noopener">Posting ↗</a>'
        if posting else "—"
    )
    status = app.get("status") or ""
    idle = app.get("days_idle")
    idle_cell = f"{int(idle)}d" if idle is not None else "—"
    return f"""<tr>
      <td data-l="Status" class="nw"><span class="pill">{_e(STATUS_EMOJI.get(status, ''))} {_e(STATUSES.get(status, status))}</span></td>
      <td data-l="Company" class="strong">{company_cell}</td>
      <td data-l="Job ID" class="nw">{_e(_ats_job_id(app))}</td>
      <td data-l="Role">{_e(app.get('title'))}</td>
      <td data-l="Location" class="muted">{_e(app.get('location'))}</td>
      <td data-l="Applied" class="nw">{_fmt_date(app.get('applied_on'))}</td>
      <td data-l="Next" class="nw">{_fmt_date(app.get('next_important_date'))}</td>
      <td data-l="Task" class="muted">{_e(app.get('next_important_task')) or '—'}</td>
      <td data-l="Contact" class="muted">{_e(app.get('poc')) or '—'}</td>
      <td data-l="Idle" class="num nw">{idle_cell}</td>
      <td data-l="Link" class="actions nw">{posting_cell}</td>
    </tr>"""


def _mobile_card(app: dict) -> str:
    """Compact mobile card summary that expands to reveal all fields."""
    link = build_deep_link(app["job_id"])
    company = _e(app.get("company"))
    company_cell = (
        f'<a class="tg" href="{_e(link)}">{company}</a>' if link else company
    )
    status = app.get("status") or ""
    posting = app.get("application_link")
    posting_cell = (
        f'<a href="{_e(posting)}" target="_blank" rel="noopener">Posting ↗</a>'
        if posting else "—"
    )
    idle = app.get("days_idle")
    idle_cell = f"{int(idle)}d" if idle is not None else "—"
    details = [
        ("Role", _e(app.get("title")) or "—"),
        ("Location", _e(app.get("location")) or "—"),
        ("Job ID", _e(_ats_job_id(app))),
        ("Applied", _fmt_date(app.get("applied_on"))),
        ("Next", _fmt_date(app.get("next_important_date"))),
        ("Task", _e(app.get("next_important_task")) or "—"),
        ("Contact", _e(app.get("poc")) or "—"),
        ("Idle", idle_cell),
        ("Posting", posting_cell),
    ]
    details_html = "".join(
        f'<div class="detail-row"><span>{label}</span><span>{value}</span></div>'
        for label, value in details
    )
    return f"""<article class="mobile-card" tabindex="0" role="button" aria-expanded="false">
      <div class="mobile-card-summary">
        <div class="mobile-topline">
          <div class="mobile-company">{company_cell}</div>
          <span class="pill">{_e(STATUS_EMOJI.get(status, ''))} {_e(STATUSES.get(status, status))}</span>
        </div>
        <div class="mobile-meta">
          <span><strong>Job ID</strong> {_e(_ats_job_id(app))}</span>
          <span>{_fmt_date(app.get('applied_on'))}</span>
        </div>
      </div>
      <div class="mobile-card-details">{details_html}</div>
    </article>"""


def _summary(counts: dict) -> str:
    total = sum(counts.values())
    live = sum(counts.get(s, 0) for s in ACTIVE_STATUSES)
    cards = [
        f'<div class="stat"><span class="n">{total}</span><span class="k">total</span></div>',
        f'<div class="stat"><span class="n">{live}</span><span class="k">in play</span></div>',
    ]
    for status in STATUSES:
        if counts.get(status):
            cards.append(
                f'<div class="stat"><span class="n">{counts[status]}</span>'
                f'<span class="k">{STATUS_EMOJI[status]} {STATUSES[status].lower()}</span></div>'
            )
    return "".join(cards)


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Application tracker</title>
<style>
  :root {{
    --bg:#fbfbfa; --fg:#1a1a18; --muted:#6b6b66; --line:#e4e4e0;
    --card:#ffffff; --accent:#2f6f4f; --shadow:0 1px 2px rgba(0,0,0,.05);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#16161a; --fg:#e8e8e4; --muted:#9a9a94; --line:#2b2b31;
             --card:#1e1e23; --accent:#7fc4a0; --shadow:none; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
    font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif; padding:28px 20px 60px; }}
  header {{ max-width:1200px; margin:0 auto 20px; }}
  h1 {{ font-size:20px; margin:0 0 4px; letter-spacing:-.01em; }}
  .sub {{ color:var(--muted); font-size:13px; }}
  .stats {{ display:flex; flex-wrap:wrap; gap:10px; margin:18px 0 22px; }}
  .stat {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
    padding:10px 14px; min-width:88px; box-shadow:var(--shadow); }}
  .stat .n {{ display:block; font-size:20px; font-weight:600; }}
  .stat .k {{ display:block; font-size:11px; color:var(--muted); text-transform:lowercase; }}
  .wrap {{ max-width:1200px; margin:0 auto; overflow-x:auto;
    background:var(--card); border:1px solid var(--line); border-radius:12px; box-shadow:var(--shadow); }}
  .desktop-table {{ display:block; }}
  .mobile-stack {{ display:none; }}
  table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
  /* Text columns wrap. Real company names and role titles run long — held on
     one line they pushed the table past 2000px and buried the last columns
     behind a horizontal scroll even on a wide screen. */
  /* break-word, not anywhere: `anywhere` also shrinks the column's min-content
     width, which let the narrow columns collapse and split ordinary words down
     the middle ("intervie/w"). break-word still rescues a pathologically long
     token but leaves normal words intact. */
  th, td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line);
    white-space:normal; overflow-wrap:break-word; vertical-align:top; }}
  /* Short, self-describing values that look wrong broken across lines. */
  th.nw, td.nw {{ white-space:nowrap; overflow-wrap:normal; }}
  th:nth-child(2), td:nth-child(2) {{ min-width:120px; }}
  th:nth-child(3), td:nth-child(3) {{ min-width:170px; }}
  th {{ font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); font-weight:600; }}
  tr:last-child td {{ border-bottom:none; }}
  td.strong {{ font-weight:600; }}
  td.muted, .muted {{ color:var(--muted); }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  a {{ color:var(--accent); text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .pill {{ font-size:12px; padding:2px 8px; border-radius:999px; border:1px solid var(--line); }}
  a.tg {{ font-weight:600; }}
  .empty {{ padding:40px; text-align:center; color:var(--muted); }}
  footer {{ max-width:1200px; margin:16px auto 0; color:var(--muted); font-size:12px; }}

  /* Small laptop windows: ten columns of padding, not the content, is what
     pushes the table past the viewport here. Tightening the gutters buys back
     the ~150px rather than dropping columns from a screen wide enough to show
     them. Below 900px there is no padding left to reclaim and the card layout
     takes over instead. */
  @media (min-width: 901px) and (max-width: 1120px) {{
    body {{ padding:24px 14px 56px; }}
    table {{ font-size:12.5px; }}
    th, td {{ padding:8px 6px; }}
    th:nth-child(3), td:nth-child(3) {{ min-width:130px; }}
  }}

  /* Phone: a horizontally scrolling table hides the company link, which is the
     one thing worth tapping. Stack each application into its own compact card
     and expand it on demand for the full details. */
  @media (max-width: 900px) {{
    body {{ padding:20px 12px 48px; }}
    .stats {{ gap:8px; margin:14px 0 16px; }}
    .stat {{ min-width:0; flex:1 1 calc(25% - 6px); padding:8px 10px; }}
    .stat .n {{ font-size:17px; }}
    .stat .k {{ font-size:10px; }}
    .wrap {{ border:none; background:none; box-shadow:none; overflow:visible; }}
    .desktop-table {{ display:none; }}
    .mobile-stack {{ display:block; }}
    .mobile-card {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
                   box-shadow:var(--shadow); margin-bottom:10px; overflow:hidden; }}
    .mobile-card-summary {{ padding:10px 12px; cursor:pointer; }}
    .mobile-topline {{ display:flex; justify-content:space-between; align-items:flex-start; gap:8px; }}
    .mobile-company {{ font-size:16px; font-weight:600; line-height:1.3; flex:1 1 auto; }}
    .mobile-meta {{ display:flex; justify-content:space-between; gap:8px; color:var(--muted);
                   font-size:11px; margin-top:8px; }}
    .mobile-card-details {{ display:none; border-top:1px solid var(--line); padding:8px 12px 10px; }}
    .mobile-card.is-expanded .mobile-card-details {{ display:block; }}
    .detail-row {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px;
                  padding:6px 0; font-size:12px; border-top:1px solid var(--line); }}
    .detail-row:first-child {{ border-top:none; }}
    .detail-row span:first-child {{ color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }}
    .detail-row span:last-child {{ text-align:right; overflow-wrap:anywhere; }}
  }}
</style></head>
<body>
<header>
  <h1>Application tracker</h1>
  <div class="stats">{stats}</div>
</header>
<div class="wrap">
  <div class="desktop-table">{table}</div>
  <div class="mobile-stack">{mobile_cards}</div>
</div>
<footer>{note}</footer>
<script>
  document.querySelectorAll('.mobile-card').forEach((card) => {{
    const toggle = () => {{
      const expanded = card.classList.toggle('is-expanded');
      card.setAttribute('aria-expanded', String(expanded));
    }};
    card.addEventListener('click', toggle);
    card.addEventListener('keydown', (event) => {{
      if (event.key === 'Enter' || event.key === ' ') {{
        event.preventDefault();
        toggle();
      }}
    }});
  }});
</script>
</body></html>"""

_HEAD = """<table><thead><tr>
  <th class="nw">Status</th><th>Company</th><th>Job ID</th><th>Role</th><th>Location</th>
  <th class="nw">Applied</th><th class="nw">Next</th><th>Task</th><th>Contact</th>
  <th class="nw num">Idle</th><th class="nw"></th>
</tr></thead><tbody>"""


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(t: Optional[str] = Query(default=None)):
    """The tracker as one page."""
    if not _authorized(t):
        return HTMLResponse("Not found", status_code=404)

    apps = get_active_applications(_ALL_STATUSES, limit=_ROW_LIMIT, offset=0)
    counts = get_status_counts()

    if apps:
        table = _HEAD + "".join(_row(a) for a in apps) + "</tbody></table>"
        mobile_cards = "".join(_mobile_card(a) for a in apps)
    else:
        table = '<div class="empty">No applications yet. Apply to a job from Telegram and it will appear here.</div>'
        mobile_cards = table

    note = (
        "Deep links are disabled — the bot username could not be resolved, so company names are plain text."
        if apps and not build_deep_link(apps[0]["job_id"])
        else "Tap a company name to open that application in Telegram, where you can change its status, "
             "set a reminder, record a contact, or re-cut the resume."
    )
    return HTMLResponse(
        _PAGE.format(
            stats=_summary(counts),
            table=table,
            mobile_cards=mobile_cards,
            note=html.escape(note),
        )
    )


@router.get("/dashboard/data")
def dashboard_data(t: Optional[str] = Query(default=None)):
    """Same rows as JSON, for anything that would rather not scrape HTML."""
    if not _authorized(t):
        return JSONResponse({"error": "not found"}, status_code=404)
    apps = get_active_applications(_ALL_STATUSES, limit=_ROW_LIMIT, offset=0)
    for app in apps:
        app["deep_link"] = build_deep_link(app["job_id"])
        app["status_label"] = status_label(app.get("status"))
    return JSONResponse({"count": len(apps), "applications": jsonable(apps)})


def jsonable(apps: list) -> list:
    """Dates and datetimes out of MySQL are not JSON-serialisable on their own."""
    out = []
    for app in apps:
        row = {}
        for key, value in app.items():
            row[key] = value.isoformat() if hasattr(value, "isoformat") else value
        out.append(row)
    return out
