from __future__ import annotations

import datetime as dt
import shutil
import threading
from pathlib import Path
from typing import Callable

from core.util import now_iso, read_json, short_path, write_json


class RestoreError(Exception):
    pass


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


_LOCK = threading.Lock()
_HANDED_OUT: set[Path] = set()


def _free(archive: Path, label: str, reserve: bool) -> Path:
    """A manifest folder no other plan has been given. A dry run gets the plain name and reserves nothing."""
    base = f"{stamp()}-{label}"
    if not reserve:
        return archive / base
    with _LOCK:
        path, n = archive / base, 1
        while path in _HANDED_OUT or path.exists():
            n += 1
            path = archive / f"{base}-{n}"
        _HANDED_OUT.add(path)
        return path


def dig(d: dict, keys: list, create=False):
    for k in keys:
        if k not in d:
            if not create:
                return None
            d[k] = {}
        d = d[k]
    return d


class Plan:
    def __init__(self, apply: bool, label: str, archive: Path, on_applied=None, undo_hint=None, out=print):
        self.apply, self.steps = apply, []
        self.on_applied, self.undo_hint, self.out = on_applied, undo_hint, out
        self.dir = _free(archive, label, apply)
        self.manifest = {"created": now_iso(), "label": label, "entries": []}

    def say(self, msg: str) -> None:
        self.steps.append(msg)
        self.out(("  " if self.apply else "  [dry-run] ") + msg)

    def backup_json(self, path: Path) -> None:
        dest = self.dir / "json-backups" / (str(path).strip("/").replace("/", "__"))
        if not any(e.get("backup_of") == str(path) for e in self.manifest["entries"]):
            self.manifest["entries"].append({"op": "json_backup", "backup_of": str(path), "backup": str(dest)})
            if self.apply:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)

    def finish(self) -> Path | None:
        if self.apply and self.manifest["entries"]:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self.dir / "manifest.json"
            write_json(path, self.manifest, indent=2)
            self.out(f"\nManifest: {short_path(path)}")
            if self.undo_hint:
                self.out(f"Undo:     {self.undo_hint(self.dir.name)}")
            if self.on_applied:
                self.on_applied()
            return path
        if not self.apply:
            self.out("\nNothing changed. Re-run with --apply to execute.")
        return None


def _pre(apply: bool) -> str:
    return "  " if apply else "  [dry-run] "


def _move(e: dict, apply: bool, out: Callable) -> bool:
    out(f"{_pre(apply)}move {short_path(e['to'])} -> {short_path(e['from'])}")
    if apply and Path(e["to"]).exists():
        Path(e["from"]).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(e["to"], e["from"])
    return True


def _json_del(e: dict, apply: bool, out: Callable) -> bool:
    out(f"{_pre(apply)}re-add {e['name']} to {short_path(e['file'])}")
    if apply:
        data = read_json(Path(e["file"]), {}) or {}
        dig(data, e["keys"], create=True)[e["name"]] = e["value"]
        write_json(Path(e["file"]), data, indent=2)
    return True


def _json_add(e: dict, apply: bool, out: Callable) -> bool:
    out(f"{_pre(apply)}remove {e['name']} from {short_path(e['file'])}")
    if apply:
        data = read_json(Path(e["file"]), {}) or {}
        (dig(data, e["keys"]) or {}).pop(e["name"], None)
        write_json(Path(e["file"]), data, indent=2)
    return True


def _json_set(e: dict, apply: bool, out: Callable) -> bool:
    out(f"{_pre(apply)}reset {e['name']} in {short_path(e['file'])} to {e['prev']!r}")
    if apply:
        data = read_json(Path(e["file"]), {}) or {}
        tgt = dig(data, e["keys"], create=True)
        if e["prev"] is None:
            tgt.pop(e["name"], None)
        else:
            tgt[e["name"]] = e["prev"]
        write_json(Path(e["file"]), data, indent=2)
    return True


HANDLERS: dict[str, Callable[[dict, bool, Callable], bool]] = {
    "move": _move, "json_del": _json_del, "json_add": _json_add, "json_set": _json_set}


def restore(name: str, apply: bool, archive: Path, handlers: dict | None = None, on_applied=None, out=print) -> None:
    table = {**HANDLERS, **(handlers or {})}
    dirs = sorted(d for d in archive.iterdir() if (d / "manifest.json").exists()) if archive.exists() else []
    d = dirs[-1] if name == "last" and dirs else archive / name
    man = read_json(d / "manifest.json")
    if not man:
        raise RestoreError(f"no manifest in {d}")
    for e in man["entries"]:
        if e["op"] not in table and e["op"] != "json_backup":
            raise RestoreError(f"no undo for step {e['op']!r}; nothing was changed")
    out(f"Restoring {d.name}:")
    for e in reversed(man["entries"]):
        fn = table.get(e["op"])
        if fn and not fn(e, apply, out):
            raise RestoreError("restore stopped; nothing after this step was changed")
    if apply:
        (d / "manifest.json").rename(d / "manifest.restored.json")
        if on_applied:
            on_applied()
    else:
        out("\nNothing changed. Re-run with --apply to execute.")
