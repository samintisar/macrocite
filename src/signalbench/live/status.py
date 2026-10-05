"""The scan's status (spec 05): `scan status`, the stale check the scan script runs, and the
bot's /status."""

from collections.abc import Callable
from datetime import datetime, timedelta

from sqlmodel import Session, col, select

from signalbench.db.models import LiveConfig, LiveRiskState, ScanRun
from signalbench.live.book import NEW_YORK


def _when(moment: datetime) -> str:
    return f"{moment.astimezone(NEW_YORK):%Y-%m-%d %H:%M} New York"


def last_scan(session: Session, *, ok: bool = False) -> ScanRun | None:
    query = select(ScanRun)
    if ok:
        query = query.where(ScanRun.status == "ok")
    return session.exec(query.order_by(col(ScanRun.started_at).desc())).first()


def scan_stale_message(session: Session, now: datetime, days: int) -> str | None:
    """A warning when no scan has succeeded for more than `days` days (counted from `live
    start` when none has yet); None otherwise, or before `live start`."""
    last = last_scan(session, ok=True)
    live = session.get(LiveConfig, 1)
    since = last.started_at if last is not None else (None if live is None else live.created_at)
    if since is None or now - since <= timedelta(days=days):
        return None
    when = "never" if last is None else f"on {_when(last.started_at)}"
    return f"STALE: the last ok scan was {when}, more than {days} days ago."


def scan_status_lines(session: Session, next_run: Callable[[], str | None]) -> list[str]:
    """The last scan and its time, the next scheduled run, the live config and its sha256,
    the code version (the last scan's git sha), and the pause state."""
    last = last_scan(session)
    if last is None:
        lines = ["Last scan: none yet"]
    else:
        result = last.status
        if last.status == "failed":
            result = f"FAILED at step {last.failed_step}: {last.error}"
        lines = [f"Last scan: {last.as_of} {result}, started {_when(last.started_at)}"]
        ok = last_scan(session, ok=True)
        if ok is not None and ok.id != last.id:
            lines.append(f"Last ok scan: {ok.as_of}, started {_when(ok.started_at)}")
    lines.append(f"Next scheduled scan: {next_run() or 'unknown (the task is not registered)'}")
    live = session.get(LiveConfig, 1)
    if live is None:
        lines.append("Live config: none (run `signalbench live start`)")
    else:
        code = "no scan yet" if last is None or last.git_sha is None else last.git_sha[:8]
        lines.append(
            f"Live config: {live.config_path} · sha256 {live.config_sha256[:8]} · code {code}"
        )
    risk = session.get(LiveRiskState, 1)
    paused = risk is not None and risk.paused
    lines.append(
        f"New entries: PAUSED since {risk.paused_at} (/resume)" if paused and risk is not None
        else "New entries: not paused"
    )
    return lines
