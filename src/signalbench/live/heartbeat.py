"""The bot's heartbeat (spec 05): the bot rewrites one row at least every 10 minutes while it
polls, and the evening scan warns when it is more than an hour old, so a bot that is down
without exiting is noticed."""

from datetime import datetime, timedelta

from sqlmodel import Session

from signalbench.db.models import BotHeartbeat
from signalbench.live.book import NEW_YORK

STALE_AFTER = timedelta(hours=1)


def write_heartbeat(session: Session, now: datetime) -> None:
    beat = session.get(BotHeartbeat, 1)
    if beat is None:
        beat = BotHeartbeat(beat_at=now)
    beat.beat_at = now
    session.add(beat)
    session.commit()


def heartbeat_problem(session: Session, now: datetime) -> str | None:
    """Why the bot looks down (never checked in, or not for over an hour), or None."""
    beat = session.get(BotHeartbeat, 1)
    if beat is None:
        return "the bot has never checked in: is `signalbench bot run` running?"
    age = now - beat.beat_at
    if age <= STALE_AFTER:
        return None
    hours = age.total_seconds() / 3600
    return (
        f"the bot last checked in {hours:.1f} hours ago "
        f"({beat.beat_at.astimezone(NEW_YORK):%Y-%m-%d %H:%M} New York time): is it running?"
    )
