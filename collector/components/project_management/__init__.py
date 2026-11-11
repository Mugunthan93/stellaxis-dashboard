from __future__ import annotations

from core.component import Component
from core.util import write_json

from . import centre


def index_reader(ctx) -> None:
    """workspaces.json from the pass inputs; a pass without sessions leaves it alone."""
    if "sessions" not in ctx.inputs:
        return
    centre.clear_stale(centre.live(centre._bin(ctx.cfg)))
    write_json(ctx.home / "workspaces.json", centre.build_index(
        ctx.inputs["sessions"], ctx.inputs["folders"], centre.app_records(centre.app_base()), ctx.cfg))


def handle(handler, method: str, ctx) -> bool:
    return centre.handle(handler, method, ctx.cfg)


COMPONENT = Component(name="project_management", readers=[index_reader], handle=handle)
