"""Application-tracking vocabulary — the states an application moves through.

Kept in one place because four callers need to agree on it: the formatter
renders the labels, the callback router validates them, the repository filters
on them, and the reminder loop decides which ones are still worth chasing.

Statuses are stored in application_status.status as their long name. The short
codes exist only for callback_data, which Telegram caps at 64 bytes — see
common.bot.router for the grammar that uses them.
"""

from typing import Optional

# Long name -> display label. Order is the order buttons are rendered in.
STATUSES = {
    "applied": "Applied",
    "screening": "Screening",
    "interview": "Interview",
    "offer": "Offer",
    "rejected": "Rejected",
    "ghosted": "Ghosted",
}

STATUS_EMOJI = {
    "applied": "📮",
    "screening": "📞",
    "interview": "💬",
    "offer": "🎉",
    "rejected": "❌",
    "ghosted": "👻",
}

# Short code -> long name, for callback_data.
STATUS_CODES = {
    "app": "applied",
    "scr": "screening",
    "int": "interview",
    "off": "offer",
    "rej": "rejected",
    "gho": "ghosted",
}
CODE_BY_STATUS = {name: code for code, name in STATUS_CODES.items()}

# Still in play — these are what /active lists and what the reminder loop
# chases. An offer counts as open: it is the one state that most needs a
# reply. Ghosted is kept separate from rejected on purpose; a company that
# never answered is a different signal from one that said no, even though
# neither is a live application.
ACTIVE_STATUSES = ("applied", "screening", "interview", "offer")
TERMINAL_STATUSES = ("rejected", "ghosted")

# How long a status may sit untouched before the reminder loop flags it.
# Applied is the longest because early silence is normal; an interview that
# has gone quiet for a week is the one actually worth a nudge.
STALE_AFTER_DAYS = {
    "applied": 14,
    "screening": 10,
    "interview": 7,
    "offer": 3,
}

# Reminder presets. Code -> (label, days from today).
REMINDER_PRESETS = {
    "3d": ("+3 days", 3),
    "1w": ("+1 week", 7),
    "2w": ("+2 weeks", 14),
}

# Canned follow-up tasks, so the common case needs no typing. Free text goes
# through the same force-reply path as the POC field.
TASK_PRESETS = {
    "fu": "Follow up with recruiter",
    "prep": "Prep for interview",
    "ty": "Send thank-you note",
    "chk": "Check application portal",
}


def status_label(status: Optional[str]) -> str:
    """Render a stored status as '<emoji> <Label>'. Unknown values pass through."""
    if not status:
        return "—"
    label = STATUSES.get(status, status.capitalize())
    emoji = STATUS_EMOJI.get(status, "")
    return f"{emoji} {label}".strip()


def is_valid_status(status: str) -> bool:
    return status in STATUSES
