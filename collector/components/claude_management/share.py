"""The weekly share of the Claude plan for each project: the week arithmetic and the settings kept in the store."""
from __future__ import annotations

import datetime as dt
import json
import re
from contextlib import closing

from core.app import StoreError

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


def over(use: float, allowance) -> bool:
    """True when the project has an allowance, has used something, and has used all of it."""
    return allowance is not None and use > 0 and use >= allowance


def _line(project: str, name: str, percent, use: float, budget, warned_at) -> dict:
    allowance = None if budget is None or percent is None else round(budget * percent / 100, 4)
    use = round(use, 4)
    return {"id": project, "name": name, "percent": percent, "allowance": allowance, "use": use,
            "left": None if allowance is None else round(allowance - use, 4), "over": over(use, allowance), "warned_at": warned_at}


def view(ctx, names: dict, now: dt.datetime) -> dict:
    """The answer of GET /api/share: this week's use of each project against its share, and the past weeks as they were closed.
    It reads the store only, so it raises StoreError when the store is down. `names` is `project_names(ctx)`."""
    with closing(ctx.db()) as db:
        plan, pct = setting(db), percents(db)
        start, end = (iso(t) for t in bounds(now, plan["day"], plan["hour"]))
        row = db.execute("SELECT updated FROM claude_management_share_weeks WHERE starts = ?", (start,)).fetchone()
        use = dict(db.execute("SELECT project, cost FROM claude_management_share_use WHERE week = ?", (start,)))
        warned = dict(db.execute("SELECT project, at FROM claude_management_share_warnings WHERE week = ?", (start,)))
        line = lambda who, name: _line(who, name, pct.get(who), use.get(who, 0.0), plan["budget"], warned.get(who))
        lines = [line(who, name) for who, name in names.items()]
        loose = None if UNASSIGNED in names or (UNASSIGNED not in use and UNASSIGNED not in pct) else line(UNASSIGNED, "Unassigned")
        past = []
        for week, ends, budget in db.execute("SELECT starts, ends, budget FROM claude_management_share_weeks "
                                             "WHERE closed = 1 AND starts < ? ORDER BY starts DESC LIMIT 12", (start,)).fetchall():
            old = db.execute("SELECT project, cost, percent FROM claude_management_share_use WHERE week = ? ORDER BY cost DESC, project",
                             (week,)).fetchall()
            at = dict(db.execute("SELECT project, at FROM claude_management_share_warnings WHERE week = ?", (week,)))
            past.append({"start": week, "end": ends, "budget": budget, "used": round(sum(c for _, c, _ in old), 4),
                         "lines": [_line(who, names.get(who, "Unassigned" if who == UNASSIGNED else who), p, c, budget, at.get(who))
                                   for who, c, p in old]})
    return {"now": iso(now), "week": {"start": start, "end": end}, "week_start": {"day": DAYS[plan["day"]], "hour": plan["hour"]},
            "budget": plan["budget"], "used": round(sum(use.values()), 4), "updated": row[0] if row else None,
            "projects": lines, "unassigned": loose, "past": past}


def update(ctx, placed: list, now: dt.datetime) -> None:
    """The pass of one collection, in one transaction: add up the cost of each project in every open week and make the
    warnings. `placed` is what Project management lists as sessions; with none, nothing changes. A closed week is never
    touched again. With the store down there is nothing to do and no error."""
    if not placed:
        return
    try:
        db = ctx.db()
    except StoreError:
        return
    project = {p["id"]: p.get("project") or UNASSIGNED for p in reversed(placed) if p.get("id")}
    stamp = iso(now)
    with closing(db), db:
        plan, pct = setting(db), percents(db)
        start, end = (iso(t) for t in bounds(now, plan["day"], plan["hour"]))
        for (week,) in db.execute("SELECT starts FROM claude_management_share_weeks WHERE starts > ?", (start,)).fetchall():
            for table in ("weeks", "use", "warnings"):
                db.execute(f"DELETE FROM claude_management_share_{table} WHERE {'starts' if table == 'weeks' else 'week'} = ?", (week,))
        for (week,) in db.execute("SELECT starts FROM claude_management_share_weeks WHERE starts < ? AND ends > ?", (start, start)).fetchall():
            db.execute("UPDATE claude_management_share_weeks SET ends = ?, closed = 0 WHERE starts = ?", (start, week))
            db.execute("DELETE FROM claude_management_share_warnings WHERE week = ?", (week,))
        db.execute("INSERT INTO claude_management_share_weeks (starts, ends, budget, closed, updated) VALUES (?, ?, ?, 0, ?) "
                   "ON CONFLICT(starts) DO UPDATE SET ends = excluded.ends, closed = 0", (start, end, plan["budget"], stamp))
        open_weeks = db.execute("SELECT starts, ends FROM claude_management_share_weeks WHERE closed = 0 ORDER BY starts").fetchall()
        rows = [(r[0], json.loads(r[1])) for r in
                db.execute("SELECT id, hours FROM claude_management_sessions WHERE last_ts >= ?", (open_weeks[0][0][:13],))]
        for week, ends in open_weeks:
            total: dict = {}
            for sid, hours in rows:
                who = project.get(sid, UNASSIGNED)
                total[who] = total.get(who, 0.0) + sum(c for h, c in hours.items() if week[:13] <= h < ends[:13])
            db.execute("DELETE FROM claude_management_share_use WHERE week = ?", (week,))
            for who in sorted(set(pct) | {w for w, c in total.items() if c > 0}):
                cost = round(total.get(who, 0.0), 4)
                db.execute("INSERT INTO claude_management_share_use (week, project, cost, percent) VALUES (?, ?, ?, ?)",
                           (week, who, cost, pct.get(who)))
                allowance = None if plan["budget"] is None or who not in pct else round(plan["budget"] * pct[who] / 100, 4)
                if over(cost, allowance):
                    db.execute("INSERT OR IGNORE INTO claude_management_share_warnings (week, project, at, cost, allowance) "
                               "VALUES (?, ?, ?, ?, ?)", (week, who, stamp, cost, allowance))
            db.execute("UPDATE claude_management_share_weeks SET budget = ?, updated = ?, closed = ? WHERE starts = ?",
                       (plan["budget"], stamp, int(ends <= stamp), week))
