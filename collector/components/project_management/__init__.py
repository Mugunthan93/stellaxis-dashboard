from __future__ import annotations

import json
from pathlib import Path

from core.component import Component
from core.server import ApiError, host_ok, read_body, send_json
from core.undo import pre
from core.util import short_path, write_json

from . import centre, old_names


def index_reader(ctx) -> None:
    """The project index from the pass inputs, and beside it the same index under the old names; a pass without sessions leaves them alone."""
    if "sessions" not in ctx.inputs:
        return
    with centre.at(ctx):
        cfg = old_names.config(ctx.cfg)
        centre.clear_stale(centre.live(centre._bin(cfg)))
        index = centre.build_index(ctx.inputs["sessions"], ctx.inputs["folders"], centre.app_records(centre.app_base()), cfg)
        write_json(centre._home() / centre.INDEX_FILE, index)
        write_json(centre._home() / old_names.INDEX_FILE, old_names.answer(index))


def _user_config(f: Path) -> dict | None:
    """The content of config.json when it is a JSON object, else None."""
    try:
        user = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    return user if isinstance(user, dict) else None


def new_names(plan, ctx) -> bool:
    with centre.at(ctx):
        return _new_names(plan, ctx)


def _new_names(plan, ctx) -> bool:
    """Move config.json to the new keys and tasks/other.md to tasks/unassigned.md, each only when it is still in the old shape."""
    f = centre._home() / "config.json"
    user = _user_config(f)
    lines = old_names.changes(user) if user is not None else []
    for line in lines:
        plan.say(line)
    if lines:
        plan.backup_json(f)
        plan.manifest["entries"].append({"op": "project_management.old_config_keys", "file": str(f)})
        if plan.apply:
            write_json(f, old_names.to_new(user), indent=2, ensure_ascii=False)
    old, new = centre._home() / "tasks" / "other.md", centre._home() / "tasks" / "unassigned.md"
    if old.exists() and new.exists():
        plan.say(f"{short_path(old)} is left where it is: {short_path(new)} already exists")
    if plan.apply:
        with centre._TASK_LOCK:
            if old.exists() and not new.exists():
                plan.say(f"move {short_path(old)} -> {short_path(new)}")
                plan.manifest["entries"].append({"op": "move", "from": str(old), "to": str(new)})
                old.rename(new)
    elif old.exists() and not new.exists():
        plan.say(f"move {short_path(old)} -> {short_path(new)}")
        plan.manifest["entries"].append({"op": "move", "from": str(old), "to": str(new)})
    if not plan.steps:
        plan.say("nothing to rename: the new names are already in use")
    return True


def _old_config_keys(entry: dict, apply: bool, out, ctx) -> bool:
    out(f"{pre(apply)}put the old keys back in {short_path(entry['file'])}")
    if apply:
        user = _user_config(Path(entry["file"]))
        if user is None:
            out(f"{pre(apply)}{short_path(entry['file'])} is missing or is not JSON: the old keys were not put back")
            return False
        write_json(Path(entry["file"]), old_names.to_old(user), indent=2, ensure_ascii=False)
    return True


def _new_names_route(handler, method: str, ctx) -> None:
    """GET (the dry run) and POST {"apply": bool} /api/projects/new-names."""
    try:
        if not host_ok(handler):
            raise ApiError(403, "only same-origin localhost requests are answered")
        apply = False
        if method == "POST":
            apply = read_body(handler).get("apply")
            if not isinstance(apply, bool):
                raise ApiError(400, '"apply" must be true or false')
        send_json(handler, 200, ctx.act("new_names", {}, apply))
    except ApiError as e:
        send_json(handler, e.status, {"error": e.msg, **e.extra})
    except OSError as e:
        send_json(handler, 500, {"error": f"{type(e).__name__}: {e}"})


def handle(handler, method: str, ctx) -> bool:
    with centre.at(ctx):
        return _handle(handler, method, ctx)


def _handle(handler, method: str, ctx) -> bool:
    cfg = old_names.config(ctx.cfg)
    path, _, query = handler.path.partition("?")
    if path == "/api/projects/new-names" and method in ("GET", "POST"):
        _new_names_route(handler, method, ctx)
        return True
    if method == "GET":
        to = old_names.page(path, query)
        if to is not None:
            handler.send_response(302)
            handler.send_header("Location", to)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return True
        if path == old_names.ROUTE:
            send_json(handler, 200, old_names.answer(centre.listing(cfg)))
            return True
    return centre.handle(handler, method, cfg, path=old_names.path(path), fix=old_names.body)


def _index(ctx) -> list:
    with centre.at(ctx):
        return centre._index().get("projects") or []


def _projects(ctx) -> list:
    with centre.at(ctx):
        return centre._entries(old_names.config(ctx.cfg).get("projects"))


def _live(ctx) -> list:
    with centre.at(ctx):
        return centre.live(centre._bin(old_names.config(ctx.cfg))) or []


def _approvals(ctx) -> list:
    with centre.at(ctx):
        return centre.pending()


centre.index_fallback = old_names.index

COMPONENT = Component(name="project_management", readers=[index_reader], handle=handle,
                      actions={"new_names": new_names}, undo={"project_management.old_config_keys": _old_config_keys},
                      offers={"index": _index, "projects": _projects, "live": _live, "approvals": _approvals})
