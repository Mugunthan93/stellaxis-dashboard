from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from core.component import Component, load
from core.store import Store
from core.undo import Plan
from core.util import now_iso


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
        return self._core.read(owner, offer, self.cfg, **args)

    def act(self, action: str, args: dict | None = None, apply: bool = False, plan: Plan | None = None) -> dict:
        return self._core.run_action(self.name, action, args or {}, apply, self.cfg, plan=plan)

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
        try:
            self.store = Store(self.home / "stellaxis.db")
        except Exception as e:
            self.error = _err(e)
            return
        for c in self.components:
            if c.error:
                continue
            try:
                self.store.ensure(c.name, c.records)
            except Exception as e:
                c.error = _err(e)

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

    def run_pass(self, cfg: dict, inputs: dict | None = None) -> dict:
        out: dict = {}
        for c in self.components:
            if c.error:
                continue
            errors = []
            for reader in c.readers:
                try:
                    reader(self.ctx(c.name, cfg, inputs))
                except Exception as e:
                    errors.append(_err(e))
            out[c.name] = {"readers": len(c.readers), "errors": errors}
        self.last_pass = {"at": now_iso(), "components": out}
        return self.last_pass

    def run_action(self, component: str, action: str, args: dict, apply: bool, cfg: dict, plan: Plan | None = None) -> dict:
        c = next((c for c in self.components if c.name == component), None)
        if c is None or c.error or action not in c.actions:
            raise KeyError(f"{component}.{action}")
        if plan is None:
            plan = Plan(apply, f"{component}-{action}", self.claude_dir / "_archive", out=lambda _: None)
        private = getattr(c.actions[action], "private", ())
        ok, error = False, None
        try:
            ok = bool(c.actions[action](plan, self.ctx(component, cfg), **args))
        except Exception as e:
            error = _err(e)
            raise
        finally:
            path = plan.finish()
            if apply:
                row = {"at": now_iso(), "component": component, "action": action, "args": {k: v for k, v in args.items() if k not in private},
                       "ok": ok, "manifest": str(path) if path else None, "error": error}
                self.home.mkdir(parents=True, exist_ok=True)
                with open(self.home / "actions.jsonl", "a") as f:
                    f.write(json.dumps(row) + "\n")
        return {"applied": apply, "ok": ok, "steps": plan.steps, "notes": plan.notes, "result": plan.result,
                "manifest": str(path) if path else None}

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
