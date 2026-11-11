from __future__ import annotations

import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

NAME = re.compile(r"[a-z][a-z0-9_]*")


@dataclass
class Component:
    name: str
    readers: list = field(default_factory=list)
    records: list = field(default_factory=list)
    actions: dict = field(default_factory=dict)
    offers: dict = field(default_factory=dict)
    reads: list = field(default_factory=list)
    handle: Callable | None = None
    error: str | None = None


def _import(folder: Path) -> Component:
    mod_name = f"stx_{folder.name}"
    spec = importlib.util.spec_from_file_location(
        mod_name, folder / "__init__.py", submodule_search_locations=[str(folder)])
    mod = importlib.util.module_from_spec(spec)
    for key in [k for k in sys.modules if k == mod_name or k.startswith(mod_name + ".")]:
        del sys.modules[key]
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    comp = getattr(mod, "COMPONENT", None)
    if not isinstance(comp, Component):
        raise TypeError("COMPONENT is missing or is not a Component")
    if not NAME.fullmatch(comp.name):
        raise ValueError(f"bad component name {comp.name!r}")
    if comp.name != folder.name:
        raise ValueError(f"component name {comp.name!r} differs from folder name {folder.name!r}")
    return comp


def load(directory: Path) -> list[Component]:
    """Import every component folder of `directory`, in name order. Never raises."""
    out: list[Component] = []
    try:
        folders = sorted(p for p in Path(directory).iterdir() if (p / "__init__.py").is_file())
    except OSError:
        return out
    for folder in folders:
        try:
            out.append(_import(folder))
        except KeyboardInterrupt:
            raise
        except BaseException as e:
            out.append(Component(name=folder.name, error=f"{type(e).__name__}: {e}"))
    return out
