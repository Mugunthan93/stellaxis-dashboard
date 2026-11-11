"""The weekly share of the Claude plan for each project: the week arithmetic and the settings kept in the store."""
from __future__ import annotations

import datetime as dt
import re

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
FIRST_START = (3, 21)   # Thursday 21:00 UTC is Friday 02:30 in India, the owner's answer of 2026-11-12
UNASSIGNED = "unassigned"   # the id Project management gives the folders in no project


def iso(t: dt.datetime) -> str:
    """2026-11-05T21:00:00Z"""
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_index(day: str) -> int:
    """0 for Monday ... 6 for Sunday, from a full English day name or its first three letters, any case."""
    d = day.strip().lower() if isinstance(day, str) else ""
    for i, name in enumerate(DAYS):
        if d in (name, name[:3]):
            return i
    raise ValueError(f"'{day}' is not a day of the week. Use a name like friday.")


def utc_start(day: str, time: str, utc_offset: str) -> tuple[int, int]:
    """(weekday, hour) in UTC of a week start given in local time, e.g. ("friday", "02:30", "+05:30") gives (3, 21)."""
    d = day_index(day)
    t = re.fullmatch(r"(\d\d):(\d\d)", time) if isinstance(time, str) else None
    if not t or int(t[1]) > 23 or int(t[2]) > 59:
        raise ValueError(f"'{time}' is not a time. Use HH:MM on the 24-hour clock, like 02:30.")
    o = re.fullmatch(r"([+-])(\d\d):(\d\d)", utc_offset) if isinstance(utc_offset, str) else None
    if not o or int(o[2]) > 23 or int(o[3]) > 59:
        raise ValueError(f"'{utc_offset}' is not a UTC offset. Use +HH:MM or -HH:MM, like +05:30.")
    local = int(t[1]) * 60 + int(t[2])
    utc = (d * 1440 + local - (1 if o[1] == "+" else -1) * (int(o[2]) * 60 + int(o[3]))) % 10080
    if utc % 60:
        back, ahead = (local - utc % 60) % 1440, (local + 60 - utc % 60) % 1440
        raise ValueError(
            f"{DAYS[d].capitalize()} {time} at {utc_offset} is {DAYS[utc // 1440].capitalize()} {utc % 1440 // 60:02d}:{utc % 60:02d} UTC. "
            "The week must start on a whole UTC hour, because the cost is kept for whole UTC hours. "
            f"The nearest times that work are {back // 60:02d}:{back % 60:02d} and {ahead // 60:02d}:{ahead % 60:02d}.")
    return utc // 1440, utc % 1440 // 60


def bounds(now: dt.datetime, day: int, hour: int) -> tuple[dt.datetime, dt.datetime]:
    """The week `now` is in: the last moment at or before `now` that is weekday `day`, `hour`:00 UTC, and that plus 7 days."""
    now = now.astimezone(dt.timezone.utc)
    start = now.replace(hour=hour, minute=0, second=0, microsecond=0) - dt.timedelta(days=(now.weekday() - day) % 7)
    if start > now:
        start -= dt.timedelta(days=7)
    return start, start + dt.timedelta(days=7)


def setting(db) -> dict:
    """{"budget": float or None, "day": int, "hour": int}: the plan row, or the first week start with no budget."""
    r = db.execute("SELECT budget, start_day, start_hour FROM claude_management_share_plan WHERE id = 1").fetchone()
    return {"budget": r[0], "day": r[1], "hour": r[2]} if r else {"budget": None, "day": FIRST_START[0], "hour": FIRST_START[1]}


def percents(db) -> dict:
    """{project id: percent}"""
    return {r[0]: r[1] for r in db.execute("SELECT project, percent FROM claude_management_share_percents")}


def project_names(ctx) -> dict:
    """{id: name} of the projects Project management lists, in its order; the first of an id wins; {} when it did not load."""
    try:
        rows = ctx.read("project_management", "projects")
    except RuntimeError:
        return {}
    out: dict = {}
    for p in rows:
        if p.get("id") and p["id"] not in out:
            out[p["id"]] = p.get("name") or p["id"]
    return out
