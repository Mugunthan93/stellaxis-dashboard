from __future__ import annotations

import importlib.util
import re
import sys
import threading
import types
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

NAME = re.compile(r"[a-z][a-z0-9_]*")

_IMPORT_LOCK = threading.RLock()


@dataclass
class Component:
    name: str
    readers: list = field(default_factory=list)
    records: list = field(default_factory=list)
    actions: dict = field(default_factory=dict)
    offers: dict = field(default_factory=dict)
    reads: list = field(default_factory=list)
    undo: dict = field(default_factory=dict)   # step name -> handler(entry: dict, apply: bool, out) -> bool
    handle: Callable | None = None
    error: str | None = None


def _purge(mod_name: str) -> None:
    for key in [k for k in sys.modules if k == mod_name or k.startswith(mod_name + ".")]:
        del sys.modules[key]


def import_folder(folder: Path) -> types.ModuleType:
    """The package of a component folder, imported once as `stx_<folder name>`."""
    folder = Path(folder)
    with _IMPORT_LOCK:
        mod_name = f"stx_{folder.name}"
        init = folder / "__init__.py"
        have = sys.modules.get(mod_name)
        if have is not None and getattr(have, "__file__", None) and Path(have.__file__).resolve() == init.resolve():
            return have
        spec = importlib.util.spec_from_file_location(
            mod_name, init, submodule_search_locations=[str(folder)])
        mod = importlib.util.module_from_spec(spec)
        _purge(mod_name)
        sys.modules[mod_name] = mod
        try:
            spec.loader.exec_module(mod)
        except BaseException:
            _purge(mod_name)
            raise
        return mod


def _import(folder: Path) -> Component:
    comp = getattr(import_folder(folder), "COMPONENT", None)
    if not isinstance(comp, Component):
        raise TypeError("COMPONENT is missing or is not a Component")
    if not NAME.fullmatch(comp.name):
        raise ValueError(f"bad component name {comp.name!r}")
    if comp.name != folder.name:
        raise ValueError(f"component name {comp.name!r} differs from folder name {folder.name!r}")
    for step in comp.undo:
        if not step.startswith(comp.name + "."):
            raise ValueError(f"undo step {step!r} must start with {comp.name + '.'!r}")
    return replace(comp)


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
