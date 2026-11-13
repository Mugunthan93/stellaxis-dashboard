"""The actions that set the weekly budget, the week start and the percent of a project, and the undo of each."""
from __future__ import annotations

import datetime as dt
import math
from contextlib import closing
from urllib.parse import urlsplit

from core.app import StoreError
from core.server import ApiError, host_ok, read_body, send_json
from core.undo import pre
from core.util import now

from . import share
from .share import DAYS, FIRST_START, UNASSIGNED


def _number(x) -> bool:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    try:
        return math.isfinite(x)
    except OverflowError:   # an int too large for a float
        return False


def _money(x) -> str:
    return "not set" if x is None else f"${x:,.2f}"


def _percent(x) -> str:
    return "no share" if x is None else (str(int(x)) if x == int(x) else repr(x)) + "%"


def _week(day: int, hour: int) -> str:
    return f"{DAYS[day].capitalize()} {hour:02d}:00 UTC"


def _label(names: dict, project: str) -> str:
    """PartHub (parthub); a project that left the config is named by its id."""
    return f"{names.get(project, 'Unassigned' if project == UNASSIGNED else project)} ({project})"


def mac_offset() -> str:
    """The Mac's own UTC offset at this moment, as +HH:MM."""
    minutes = int(dt.datetime.now().astimezone().utcoffset().total_seconds() // 60)
    return f"{'+' if minutes >= 0 else '-'}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


def _put(db, column: str, *values) -> None:
    """Write the plan row, made with the first week start when it is missing."""
    db.execute("INSERT OR IGNORE INTO claude_management_share_plan (id, budget, start_day, start_hour) VALUES (1, NULL, ?, ?)", FIRST_START)
    db.execute(f"UPDATE claude_management_share_plan SET {column} WHERE id = 1", values)


def _put_percent(db, project: str, value) -> None:
    if value is None:
        db.execute("DELETE FROM claude_management_share_percents WHERE project = ?", (project,))
    else:
        db.execute("INSERT OR REPLACE INTO claude_management_share_percents (project, percent) VALUES (?, ?)", (project, value))


def set_budget(plan, ctx, budget) -> bool:
    if budget is not None and not (_number(budget) and budget > 0):
        plan.note("The weekly budget must be a number above 0, or none to remove it.")
        return False
    new = None if budget is None else float(budget)
    with closing(ctx.db()) as db, db:
        prev = share.setting(db)["budget"]
        plan.result = {"what": "budget", "prev": prev, "new": new}
        if new == prev:
            plan.say(f"weekly budget: stays {_money(prev)}")
            return True
        plan.say(f"weekly budget: {_money(prev)} becomes {_money(new)}")
        if plan.apply:
            _put(db, "budget = ?", new)
            plan.manifest["entries"].append({"op": "claude_management.share_setting", "what": "budget", "prev": prev, "new": new})
    return True


def set_week_start(plan, ctx, day, time, utc_offset=None) -> bool:
    offset = utc_offset or mac_offset()
    try:
        new = list(share.utc_start(day, time, offset))
    except ValueError as e:
        plan.note(str(e))
        return False
    with closing(ctx.db()) as db, db:
        s = share.setting(db)
        prev = [s["day"], s["hour"]]
        plan.result = {"what": "week_start", "prev": prev, "new": new}
        if new == prev:
            plan.say(f"week start: stays {_week(*prev)}")
            return True
        plan.say(f"week start: {_week(*prev)} becomes {_week(*new)} ({DAYS[share.day_index(day)].capitalize()} {time} at {offset})")
        plan.note("The weeks are added up again at the next collection.")
        if plan.apply:
            _put(db, "start_day = ?, start_hour = ?", *new)
            plan.manifest["entries"].append({"op": "claude_management.share_setting", "what": "week_start", "prev": prev, "new": new})
    return True


def set_percent(plan, ctx, project, percent) -> bool:
    if percent is not None and not (_number(percent) and 0 <= percent <= 100):
        plan.note("The percent must be a number from 0 to 100, or none to remove the share.")
        return False
    names = share.project_names(ctx)
    ids = list(names) + ([] if UNASSIGNED in names else [UNASSIGNED])
    new, label = None if percent is None else float(percent), _label(names, project)
    with closing(ctx.db()) as db, db:
        every = share.percents(db)
        if project not in ids and not (new is None and project in every):   # the share of a project that left the config can still be removed
            plan.note(f"No project has the id '{project}'. The ids are: {', '.join(ids)}.")
            return False
        prev = every.get(project)
        plan.result = {"what": "percent", "project": project, "prev": prev, "new": new}
        if new == prev:
            plan.say(f"{label}: stays {_percent(prev)}")
            return True
        plan.say(f"{label}: {_percent(prev)} becomes {_percent(new)}" + ("" if new is None else " of the weekly budget"))
        every.pop(project, None)
        total = round(sum(every.values()) + (new or 0), 4)
        if total > 100:
            plan.note(f"The shares now add up to {_percent(total)}. That is allowed.")
        if plan.apply:
            _put_percent(db, project, new)
            plan.manifest["entries"].append({"op": "claude_management.share_setting", "what": "percent", "project": project,
                                             "prev": prev, "new": new})
    return True


def undo_setting(entry: dict, apply: bool, out, ctx) -> bool:
    """Put the value the action replaced back. A value changed again since the action is still put back, and the line says so."""
    what, prev, new = entry["what"], entry["prev"], entry["new"]
    with closing(ctx.db()) as db, db:
        if what == "budget":
            now, text = share.setting(db)["budget"], _money
            label = "weekly budget"
        elif what == "week_start":
            s = share.setting(db)
            now, text = [s["day"], s["hour"]], lambda v: _week(*v)
            label = "week start"
        else:
            project = entry["project"]
            now, text = share.percents(db).get(project), _percent
            label = _label(share.project_names(ctx), project)
        out(f"{pre(apply)}{label}: {text(now)} goes back to {text(prev)}" + ("" if now == new else " (it was changed again after this action)"))
        if apply:
            if what == "budget":
                _put(db, "budget = ?", prev)
            elif what == "week_start":
                _put(db, "start_day = ?, start_hour = ?", *prev)
            else:
                _put_percent(db, project, prev)
    return True


ROUTES = {"/api/share/budget": ("set_budget", ("budget",), ()),
          "/api/share/week-start": ("set_week_start", ("day", "time"), ("utc_offset",)),
          "/api/share/percent": ("set_percent", ("project", "percent"), ())}


def handle(handler, method: str, ctx) -> bool:
    """Serve /api/share*. Returns False when the path is not ours."""
    path = urlsplit(handler.path).path
    if path != "/api/share" and not path.startswith("/api/share/"):
        return False
    try:
        if not host_ok(handler):
            raise ApiError(403, "share API only answers same-origin localhost requests")
        if method == "GET" and path == "/api/share":
            send_json(handler, 200, share.view(ctx, share.project_names(ctx), now()))
            return True
        if method != "POST" or path not in ROUTES:
            raise ApiError(404, "no such share endpoint")
        action, needed, optional = ROUTES[path]
        body = read_body(handler)
        if not isinstance(body.get("apply"), bool):
            raise ApiError(400, '"apply" must be true or false')
        for key in needed:
            if key not in body:
                raise ApiError(400, f'"{key}" is missing')
        done = ctx.act(action, {k: body[k] for k in needed + optional if k in body}, body["apply"])
        if not done["ok"]:
            raise ApiError(400, " ".join(done["notes"]), **done)
        send_json(handler, 200, done)
    except ApiError as e:
        send_json(handler, e.status, {"error": e.msg, **e.extra})
    except StoreError as e:
        send_json(handler, 503, {"error": f"The store is down: {e}"})
    return True
