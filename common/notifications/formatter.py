"""Telegram message formatting and inline-keyboard builders.

Pure functions — no I/O, no DB calls, no Telegram API calls.
"""

import html as _html
import re as _re
from typing import Dict, List, Optional

from common.applications import (
    CODE_BY_STATUS,
    REMINDER_PRESETS,
    STATUS_CODES,
    STATUS_EMOJI,
    STATUSES,
    TASK_PRESETS,
    status_label,
)


def format_job_message(job: Dict[str, str], index: int = 0, total: int = 0,
                       borg_name: str = "") -> str:
    """Format a single job as an HTML message body."""
    company = job.get("company", "Unknown")
    title = job.get("title", "Unknown")
    location = job.get("location", "Unknown")
    keywords = job.get("keywords", [])
    link = job.get("application_link", "")

    header = ""
    if borg_name and total:
        header = f"🔎 <b>{borg_name.capitalize()}</b> | {index} of {total}\n\n"

    lines = [
        f"🏢 <b>{company}</b>",
        f"📌 {title}",
        f"📍 {location}",
        f"🏷 {', '.join(keywords) if keywords else 'none'}",
    ]
    if link:
        lines.append(f'🔗 <a href="{link}">Apply</a>')
    return header + "\n".join(lines)


def format_applied_message(job: dict,
                           referral_search_url: Optional[str] = None) -> str:
    """Format a job message after the user has applied.

    Only the applied case is rendered. A rejected job is never re-rendered:
    the sweeper decides it hours after the fact, by which point the original
    notification has aged out of the chat.
    """
    company = job.get("company", "Unknown")
    title = job.get("title", "Unknown")
    location = job.get("location", "Unknown")
    link = job.get("application_link", "")

    lines = [
        "<b>✅ APPLIED</b>\n",
        f"🏢 <b>{company}</b>",
        f"📌 {title}",
        f"📍 {location}",
    ]
    if link:
        lines.append(f'🔗 <a href="{link}">Apply</a>')
    if referral_search_url:
        lines.append(f'🔍 <a href="{referral_search_url}">Find referrers at {company} on LinkedIn</a>')
    return "\n".join(lines)


def make_inline_keyboard(job_id: int) -> dict:
    """Build an InlineKeyboardMarkup with the Apply button.

    Apply is the only action. Declining is expressed by ignoring the message —
    common.sweeper rejects anything left undecided past the response window.
    """
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Apply", "callback_data": f"apply:{job_id}"},
            ]
        ]
    }


# ---------------------------------------------------------------------------
# Application tracker screens
#
# Every screen below renders into the *same* Telegram message: the card swaps
# itself for a picker and back via editMessageText, so tracking a job costs one
# message in the chat rather than one per interaction.
#
# All callback_data follows "<verb>[:<job_id>[:<value>]]" and carries
# everything the handler needs. Nothing is remembered between taps — the bot
# process owns the sweeper thread and gets restarted, and server-side
# conversation state would not survive that.
# ---------------------------------------------------------------------------

# Marker hidden in the POC prompt so the force-reply that answers it can be
# tied back to a job. The reply update carries the prompt as reply_to_message,
# which makes the job id part of the message itself rather than server state.
_POC_MARKER = _re.compile(r"\[job:(\d+)\]")

# Telegram renders long button labels badly rather than rejecting them.
_BUTTON_TEXT_LIMIT = 40


def _esc(value) -> str:
    """Escape ATS-supplied text for HTML parse_mode.

    Titles and locations come from third-party feeds and do contain '&'.
    Unescaped, Telegram rejects the whole sendMessage as malformed HTML.
    """
    return _html.escape(str(value)) if value else ""


def _fmt_date(value) -> str:
    """Render a DATE column for display. Accepts date, str, or None."""
    if not value:
        return "—"
    if hasattr(value, "strftime"):
        return value.strftime("%d %b %Y")
    return str(value)


def _fmt_age(days) -> str:
    """'today' / 'yesterday' / 'N days ago' for a DATEDIFF result."""
    if days is None:
        return ""
    days = int(days)
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def _truncate(text: str, limit: int = _BUTTON_TEXT_LIMIT) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_job_card(app: dict) -> str:
    """The tracker's home screen for one application."""
    applied = _fmt_date(app.get("applied_on"))
    age = _fmt_age(app.get("days_since_applied"))
    lines = [
        f"{status_label(app.get('status'))} · <b>{_esc(app.get('company'))}</b>\n",
        f"📌 {_esc(app.get('title'))}",
        f"📍 {_esc(app.get('location'))}",
        f"📅 Applied {applied}" + (f" ({age})" if age else ""),
    ]

    task = app.get("next_important_task")
    next_date = app.get("next_important_date")
    if task or next_date:
        lines.append(f"⏰ {_esc(task) or 'Follow up'} — {_fmt_date(next_date)}")
    else:
        lines.append("⏰ No reminder set")

    lines.append(f"👤 {_esc(app.get('poc')) or 'No contact yet'}")

    link = app.get("application_link")
    if link:
        lines.append(f'\n🔗 <a href="{_esc(link)}">Job posting</a>')
    return "\n".join(lines)


def make_job_card_keyboard(job_id: int) -> dict:
    """Root menu for an application."""
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Status", "callback_data": f"st:{job_id}"},
                {"text": "⏰ Remind", "callback_data": f"rem:{job_id}"},
            ],
            [
                {"text": "👤 Contact", "callback_data": f"poc:{job_id}"},
                {"text": "📄 Resume", "callback_data": f"res:{job_id}"},
            ],
            [
                {"text": "📋 All applications", "callback_data": "lst:0"},
            ],
        ]
    }


def make_status_keyboard(job_id: int, current: str = "") -> dict:
    """Status picker. The current status is marked rather than hidden, so the
    keyboard never changes shape as an application moves."""
    buttons = []
    row = []
    for status in STATUSES:
        code = CODE_BY_STATUS[status]
        mark = "• " if status == current else ""
        row.append({
            "text": f"{mark}{STATUS_EMOJI[status]} {STATUSES[status]}",
            "callback_data": f"st:{job_id}:{code}",
        })
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([{"text": "← Back", "callback_data": f"nav:{job_id}"}])
    return {"inline_keyboard": buttons}


def make_reminder_keyboard(job_id: int) -> dict:
    """Follow-up date picker, plus a way into the task list and a clear."""
    presets = [
        {"text": label, "callback_data": f"rem:{job_id}:{code}"}
        for code, (label, _days) in REMINDER_PRESETS.items()
    ]
    return {
        "inline_keyboard": [
            presets,
            [
                {"text": "📝 What for?", "callback_data": f"tsk:{job_id}"},
                {"text": "🗑 Clear", "callback_data": f"rem:{job_id}:clr"},
            ],
            [{"text": "← Back", "callback_data": f"nav:{job_id}"}],
        ]
    }


def make_task_keyboard(job_id: int) -> dict:
    """Canned follow-up tasks, so the common case needs no typing."""
    buttons = [
        [{"text": label, "callback_data": f"tsk:{job_id}:{code}"}]
        for code, label in TASK_PRESETS.items()
    ]
    buttons.append([{"text": "← Back", "callback_data": f"nav:{job_id}"}])
    return {"inline_keyboard": buttons}


def format_poc_prompt(app: dict) -> str:
    """Force-reply prompt asking for a contact name.

    The job id is embedded in the text because Telegram hands the prompt back
    as reply_to_message on the answer — which keeps the association in the
    update itself instead of in a session the bot would have to remember.
    """
    return (
        f"👤 Who is your contact at <b>{_esc(app.get('company'))}</b>?\n"
        f"<i>Reply to this message with a name.</i>\n"
        f"<code>[job:{app.get('job_id')}]</code>"
    )


def make_force_reply(placeholder: str = "") -> dict:
    markup = {"force_reply": True}
    if placeholder:
        markup["input_field_placeholder"] = placeholder
    return markup


def parse_poc_prompt(text: str):
    """Recover the job id from a POC prompt the user replied to. None if absent."""
    if not text:
        return None
    match = _POC_MARKER.search(text)
    return int(match.group(1)) if match else None


def format_application_list(apps: list, page: int, total: int, per_page: int) -> str:
    """Header for the paged list of open applications."""
    if not apps:
        return "📋 <b>No open applications</b>\n\nNothing to track yet — apply to a job and it will show up here."
    first = page * per_page + 1
    last = first + len(apps) - 1
    header = f"📋 <b>Open applications</b> ({first}–{last} of {total})"
    lines = [header, ""]
    for app in apps:
        due = ""
        if app.get("next_important_date"):
            due = f" · ⏰ {_fmt_date(app['next_important_date'])}"
        lines.append(
            f"{STATUS_EMOJI.get(app.get('status'), '•')} <b>{_esc(app.get('company'))}</b> — "
            f"{_esc(app.get('title'))}{due}"
        )
    return "\n".join(lines)


def make_list_keyboard(apps: list, page: int, total_pages: int) -> dict:
    """One button per application, then a pager."""
    buttons = [
        [{
            "text": _truncate(f"{STATUS_EMOJI.get(a.get('status'), '•')} {a.get('company')} · {a.get('title')}"),
            "callback_data": f"nav:{a['job_id']}",
        }]
        for a in apps
    ]
    if total_pages > 1:
        pager = []
        if page > 0:
            pager.append({"text": "←", "callback_data": f"lst:{page - 1}"})
        pager.append({"text": f"{page + 1}/{total_pages}", "callback_data": "nop"})
        if page < total_pages - 1:
            pager.append({"text": "→", "callback_data": f"lst:{page + 1}"})
        buttons.append(pager)
    return {"inline_keyboard": buttons}


def format_due_message(app: dict, reason: str) -> str:
    """The nudge the reminder loop sends. reason is 'due' or 'stale'."""
    if reason == "due":
        head = f"⏰ <b>Follow-up due</b> — {_esc(app.get('next_important_task')) or 'Follow up'}"
    else:
        head = f"💤 <b>No movement in {int(app.get('days_idle') or 0)} days</b>"
    return (
        f"{head}\n\n"
        f"{status_label(app.get('status'))} · <b>{_esc(app.get('company'))}</b>\n"
        f"📌 {_esc(app.get('title'))}\n"
        f"📅 Applied {_fmt_date(app.get('applied_on'))}"
    )


def format_stats(counts: dict) -> str:
    """Counts by status for /stats."""
    if not counts:
        return "📊 <b>No applications yet</b>"
    total = sum(counts.values())
    lines = [f"📊 <b>Applications</b> — {total} total", ""]
    for status in STATUSES:
        if status in counts:
            lines.append(f"{STATUS_EMOJI[status]} {STATUSES[status]}: <b>{counts[status]}</b>")
    unknown = set(counts) - set(STATUSES)
    for status in sorted(unknown):
        lines.append(f"• {_esc(status)}: <b>{counts[status]}</b>")
    return "\n".join(lines)


def status_from_code(code: str):
    """Map a callback short code back to a stored status, or None."""
    return STATUS_CODES.get(code)
