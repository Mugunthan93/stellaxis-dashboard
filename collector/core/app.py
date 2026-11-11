from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from core.component import Component, load
from core import transcripts, undo
from core.store import Store
from core.undo import Plan
from core.util import now_iso, read_json, write_json


def _err(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"


class StoreError(RuntimeError):
    pass


class Ctx:
    """What a component sees: its name and config, the store, and the offers it declared in `reads`."""

    def __init__(self, core: Core, name: str, cfg: dict, inputs: dict | None = None):
        self._core = core
        self.inputs = inputs or {}
        self.name = name
        self.cfg = cfg
        self.home = core.home
        self.claude_dir = core.claude_dir
        self.claude_json = core.claude_json
        self.service = core.service

    def db(self) -> sqlite3.Connection:
        if self._core.store is None:
            raise StoreError(self._core.error)
        return self._core.store.connect()

    def read(self, owner: str, offer: str, **args) -> list:
        me = next((c for c in self._core.components if c.name == self.name), None)
        if me is None or (owner, offer) not in [tuple(r) for r in me.reads]:
            raise LookupError(f"{self.name!r} did not declare reads of {owner}.{offer}")
        other = next((c for c in self._core.components if c.name == owner), None)
        if other is not None and other.error:   # there but broken is not removed: the reader fails, its last good files stay
            raise RuntimeError(f"{owner} did not load: {other.error}")
        return self._core.read(owner, offer, self.cfg, **args)

    def act(self, action: str, args: dict | None = None, apply: bool = False, plan: Plan | None = None) -> dict:
        return self._core.run_action(self.name, action, args or {}, apply, self.cfg, plan=plan)

    @property
    def cache(self) -> dict:
        """The pass cache (`cache.json`): bookkeeping only, never records. Loaded the first time it is asked in a pass."""
        run = self._core.running()
        if run["cache"] is None:
            got = read_json(self.home / "cache.json", {})
            run["cache"] = got if isinstance(got, dict) else {}
        return run["cache"]

    def transcripts(self) -> dict:
        """The state of every transcript file (path -> state), read one time in a pass for every component."""
        run = self._core.running()
        if run["error"] is not None:
            raise run["error"]   # a failed read poisons the pass: no second read on top of half-counted lines
        if run["files"] is None:
            consumers = [c.lines for c in self._core.components if not c.error and c.lines is not None]
            try:
                run["files"] = transcripts.read(self.claude_dir / "projects", self.cache, consumers)
            except Exception as e:
                run["error"] = e
                raise
        return run["files"]

    @property
    def memo(self) -> dict:
        """A dict of this component's own that lives for one pass; outside a pass (or on another thread) it is empty."""
        run = self._core.pass_state
        if run is None or run["thread"] != threading.get_ident():
            return {}
        return run["memos"].setdefault(self.name, {})

    def status(self, phase: str, force: bool = True, **detail) -> None:
        """Tell the way in what the pass is doing. A way in that listens gets it; nothing here ever raises."""
        fn = self._core.on_status
        if fn is not None:
            try:
                fn(phase, force, **detail)
            except Exception:
                pass


class Core:
    def __init__(self, home: Path, claude_dir: Path, components_dir: Path, claude_json: Path | None = None):
        self.home = Path(home)
        self.claude_dir = Path(claude_dir)
        self.claude_json = Path(claude_json) if claude_json else self.claude_dir / ".claude.json"
        self.service = False
        self.on_status = None
        self.offer_errors: dict[str, str] = {}
        self.components: list[Component] = load(components_dir)
        self.store: Store | None = None
        self.error: str | None = None
        self.last_pass: dict | None = None
        self.pass_state: dict | None = None   # the cache, files, memos, read error and thread of the pass that runs
        self._pass_lock = threading.RLock()
        self._not_ensured: set[str] = set()   # components whose tables failed at start: tried again at each pass
        try:
            self.store = Store(self.home / "stellaxis.db")
        except Exception as e:
            self.error = _err(e)
            return
        for c in self.components:
            if c.error:
                continue
            self._ensure(c)

    def _ensure(self, c: Component) -> None:
        try:
            self.store.ensure(c.name, c.records, c.changes)
        except Exception as e:
            c.error = _err(e)
            self._not_ensured.add(c.name)
        else:
            c.error = None
            self._not_ensured.discard(c.name)

    def ctx(self, name: str, cfg: dict, inputs: dict | None = None) -> Ctx:
        return Ctx(self, name, cfg, inputs)

    def read(self, owner: str, offer: str, cfg: dict, **args) -> list:
        """For ways in: what `owner` offers. No declaration needed."""
        other = next((c for c in self.components if c.name == owner), None)
        if other is None or other.error or offer not in other.offers:
            return []
        key = f"{owner}.{offer}"
        try:
            got = other.offers[offer](self.ctx(owner, cfg), **args)
        except Exception as e:
            self.offer_errors[key] = _err(e)
            return []
        self.offer_errors.pop(key, None)
        return got

    def undo_handlers(self, cfg: dict | None = None) -> dict:
        """step -> (entry, apply, out) -> bool, each handler given the ctx of its component."""
        def bind(fn, ctx):
            return lambda entry, apply, out: fn(entry, apply, out, ctx)
        return {step: bind(fn, self.ctx(c.name, cfg or {}))
                for c in self.components if not c.error for step, fn in c.undo.items()}

    def running(self) -> dict:
        if self.pass_state is None or self.pass_state["thread"] != threading.get_ident():
            raise RuntimeError("only during a pass")
        return self.pass_state

    def run_pass(self, cfg: dict, inputs: dict | None = None) -> dict:
        """Run the readers in rounds: the first reader of every component (in name order), then the second, ..."""
        with self._pass_lock:
            for c in self.components:
                if c.name in self._not_ensured:   # e.g. a store that was locked at start
                    self._ensure(c)
            comps = [c for c in self.components if not c.error]
            out: dict = {c.name: {"readers": len(c.readers), "errors": []} for c in comps}
            self.pass_state = {"cache": None, "files": None, "memos": {}, "error": None,
                               "thread": threading.get_ident()}
            try:
                for i in range(max((len(c.readers) for c in comps), default=0)):
                    for c in comps:
                        if i < len(c.readers):
                            try:
                                c.readers[i](self.ctx(c.name, cfg, inputs))
                            except Exception as e:
                                out[c.name]["errors"].append(_err(e))
                if self.pass_state["cache"] is not None and self.pass_state["error"] is None:
                    write_json(self.home / "cache.json", self.pass_state["cache"])
            finally:
                self.pass_state = None
            self.last_pass = {"at": now_iso(), "components": out}
            return self.last_pass

    def run_action(self, component: str, action: str, args: dict, apply: bool, cfg: dict, plan: Plan | None = None) -> dict:
        c = next((c for c in self.components if c.name == component), None)
        if c is None or c.error or action not in c.actions:
            raise KeyError(f"{component}.{action}")
        if plan is None:
            plan = Plan(apply, f"{component}-{action}", self.claude_dir / "_archive", out=lambda _: None)
        private = getattr(c.actions[action], "private", ())
        ok, error, refused = False, None, False
        try:
            ok = bool(c.actions[action](plan, self.ctx(component, cfg), **args))
            refused = not ok and not apply   # a refused dry run is not finished: no "Nothing changed" after the refusal
        except Exception as e:
            error = _err(e)
            raise
        finally:
            path = None if refused else plan.finish()
            if apply:
                row = {"at": now_iso(), "component": component, "action": action, "args": {k: v for k, v in args.items() if k not in private},
                       "ok": ok, "manifest": str(path) if path else None, "error": error}
                self._log(row)
        return {"applied": apply, "ok": ok, "steps": plan.steps, "notes": plan.notes, "result": plan.result,
                "manifest": str(path) if path else None}

    def _log(self, row: dict) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        with open(self.home / "actions.jsonl", "a") as f:
            f.write(json.dumps(row) + "\n")

    def restore(self, name: str, apply: bool, archive: Path, handlers: dict, on_applied=None, out=print) -> None:
        """Undo one archive folder (see undo.restore). An applied restore, also one that stops with an error, writes a line to actions.jsonl."""
        folder, manifest, error = undo.folder_of(name, archive), None, None   # found before the restore renames the manifest
        try:
            undo.restore(name, apply, archive, handlers=handlers, on_applied=on_applied, out=out)
            manifest = str(folder / "manifest.restored.json")
        except Exception as e:
            error = _err(e)
            raise
        finally:
            if apply:
                self._log({"at": now_iso(), "component": "core", "action": "restore",
                           "args": {"name": name, "folder": folder.name if folder.is_dir() else None},
                           "ok": error is None, "manifest": manifest, "error": error})

    def describe(self) -> dict:
        return {
            "components": [{"name": c.name, "readers": len(c.readers), "actions": sorted(c.actions),
                            "offers": sorted(c.offers), "reads": [list(r) for r in c.reads], "error": c.error}
                           for c in self.components],
            "store": str(self.home / "stellaxis.db"),
            "error": self.error,
            "offer_errors": dict(self.offer_errors),
            "last_pass": self.last_pass,
        }
