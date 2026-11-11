from __future__ import annotations

import settings_api
from core.component import Component
from core.util import write_json

from . import centre, old_names


def index_reader(ctx) -> None:
    """The project index from the pass inputs, and beside it the same index under the old names; a pass without sessions leaves them alone."""
    if "sessions" not in ctx.inputs:
        return
    cfg = old_names.config(ctx.cfg)
    centre.clear_stale(centre.live(centre._bin(cfg)))
    index = centre.build_index(ctx.inputs["sessions"], ctx.inputs["folders"], centre.app_records(centre.app_base()), cfg)
    write_json(centre._home() / centre.INDEX_FILE, index)
    write_json(centre._home() / old_names.INDEX_FILE, old_names.answer(index))


def handle(handler, method: str, ctx) -> bool:
    cfg = old_names.config(ctx.cfg)
    path, _, query = handler.path.partition("?")
    if method == "GET":
        to = old_names.page(path, query)
        if to is not None:
            handler.send_response(302)
            handler.send_header("Location", to)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return True
        if path == old_names.ROUTE:
            settings_api._send(handler, 200, old_names.answer(centre.listing(cfg)))
            return True
    return centre.handle(handler, method, cfg, path=old_names.path(path), fix=old_names.body)


def _index(ctx) -> list:
    return centre._index().get("projects") or []


def _projects(ctx) -> list:
    return centre._entries(old_names.config(ctx.cfg).get("projects"))


def _live(ctx) -> list:
    return centre.live(centre._bin(old_names.config(ctx.cfg))) or []


def _approvals(ctx) -> list:
    return centre.pending()


COMPONENT = Component(name="project_management", readers=[index_reader], handle=handle,
                      offers={"index": _index, "projects": _projects, "live": _live, "approvals": _approvals})
