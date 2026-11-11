from __future__ import annotations

import json
from pathlib import Path

from core import transcripts
from core.component import Component
from core.server import ApiError, host_ok, read_body, send_json
from core.undo import pre
from core.util import short_path, write_json

from . import centre, old_names, records, relocate, repos


def repos_reader(ctx) -> None:
    """Round one: the repos of this Mac and the sessions of the transcripts, kept for round two; the repos also in the store."""
    if "collector" not in ctx.inputs:
        return
    with centre.at(ctx):
        files = ctx.transcripts()
        found = repos.discover(ctx, {st["cwd"] for st in files.values() if st.get("cwd")})
        ctx.memo.update(repos=found, sessions=transcripts.sessions(files))
        records.save_repos(ctx, found)


def index_reader(ctx) -> None:
    """Round two: the project index, and beside it the same index under the old names. The cost of each session comes
    from Claude management (0 when it gives none). When round one failed, the last good index stays."""
    if "collector" not in ctx.inputs or "sessions" not in ctx.memo or "repos" not in ctx.memo:
        return
    with centre.at(ctx):
        ctx.status("indexing projects")
        cfg = old_names.config(ctx.cfg)
        centre.clear_stale(centre.live(centre._bin(cfg)))
        costs = {r["id"]: r["cost"] for r in ctx.read("claude_management", "sessions")}
        rows = sorted(({"id": s["id"], "project": short_path(s["cwd"]) if s["cwd"] else "(unknown)", "first_ts": s["first_ts"],
                        "last_ts": s["last_ts"], "cost": costs.get(s["id"]) or 0, "branch": s["branch"]}
                       for s in ctx.memo["sessions"].values()), key=lambda r: r["last_ts"] or "", reverse=True)
        index = centre.build_index(rows, ctx.memo["repos"], centre.app_records(centre.app_base()), cfg)
        failed = None
        try:
            records.save_index(ctx, index, {r["id"]: i for i, r in enumerate(rows)})
            stored = records.index(ctx, costs)   # never after a failed save: the rows would be the last pass's
        except Exception as e:   # a locked or full store, or text SQLite cannot hold: the files still get this index
            stored, failed = None, e
        write_json(ctx.home / centre.INDEX_FILE, stored or index)
        write_json(ctx.home / old_names.INDEX_FILE, old_names.answer(index))
        if failed is not None:
            raise failed


def _repos(ctx) -> list:
    """The repos found: this pass's while a pass holds them, else the store's."""
    memo = ctx.memo
    return memo["repos"] if "repos" in memo else records.repos(ctx)


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

COMPONENT = Component(name="project_management", readers=[repos_reader, index_reader], handle=handle,
                      records=records.RECORDS, reads=[("claude_management", "sessions")],
                      actions={"new_names": new_names, "relocate": relocate.relocate},
                      undo={"project_management.old_config_keys": _old_config_keys,
                            "project_management.relocate": relocate.undo_relocate},
                      offers={"index": _index, "projects": _projects, "live": _live, "approvals": _approvals,
                              "repos": _repos, "sessions": records.sessions})
