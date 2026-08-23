"""Deep links into the bot — the dashboard's handoff to Telegram.

A row in the read-only dashboard is a link to t.me/<bot>?start=job_<id>.
Opening it delivers `/start job_<id>` as an ordinary message update, which the
router turns into that job's card. That replaces copying an id out of the
browser and typing it into the chat — the same handler serves both entry
points, and there is nothing to mistype.

The username is resolved once at startup via getMe rather than configured, so
it cannot drift away from whatever token the process is actually running as.
"""

from typing import Optional

from common.logger import get_logger
from common.notifications.telegram import get_me

logger = get_logger("bot.deeplinks")

# Telegram caps the ?start= payload at 64 characters from [A-Za-z0-9_-], which
# a "job_<id>" token never comes close to.
_PAYLOAD_PREFIX = "job_"

_bot_username: Optional[str] = None


def resolve_bot_username() -> Optional[str]:
    """Fetch and cache the bot's username. Call once at startup."""
    global _bot_username
    me = get_me()
    if not me:
        logger.warning("Could not resolve bot username — dashboard deep links disabled")
        return None
    _bot_username = me.get("username")
    logger.info("Bot username resolved: @%s", _bot_username)
    return _bot_username


def bot_username() -> Optional[str]:
    """The cached username, or None if getMe never succeeded."""
    return _bot_username


def build_deep_link(job_id: int) -> Optional[str]:
    """t.me link that opens this job's card, or None if the username is unknown."""
    if not _bot_username:
        return None
    return f"https://t.me/{_bot_username}?start={_PAYLOAD_PREFIX}{job_id}"


def parse_start_payload(text: str) -> Optional[int]:
    """
    Pull the job id out of a '/start job_42' command.

    Returns None for a bare /start or any payload that is not a job token, so
    the caller can fall back to a greeting.
    """
    parts = text.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    payload = parts[1].strip()
    if not payload.startswith(_PAYLOAD_PREFIX):
        return None
    try:
        return int(payload[len(_PAYLOAD_PREFIX):])
    except ValueError:
        return None
