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


class Ctx:
    """What a component sees: its name and config, the store, and the offers it declared in `reads`."""

    def __init__(self, core: Core, name: str, cfg: dict):
        self._core = core
        self.name = name
        self.cfg = cfg
        self.home = core.home

    def db(self) -> sqlite3.Connection:
        return self._core.store.connect()

    def read(self, owner: str, offer: str, **args) -> list:
        me = next((c for c in self._core.components if c.name == self.name), None)
        if me is None or (owner, offer) not in [tuple(r) for r in me.reads]:
            raise LookupError(f"{self.name!r} did not declare reads of {owner}.{offer}")
        other = next((c for c in self._core.components if c.name == owner), None)
        if other is None or other.error or offer not in other.offers:
            return []
        return other.offers[offer](self._core.ctx(owner, self.cfg), **args)


class Core:
    def __init__(self, home: Path, claude_dir: Path, components_dir: Path):
        self.home = Path(home)
        self.claude_dir = Path(claude_dir)
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

    def ctx(self, name: str, cfg: dict) -> Ctx:
        return Ctx(self, name, cfg)

    def run_pass(self, cfg: dict) -> dict:
        out: dict = {}
        if self.store is not None:
            for c in self.components:
                if c.error:
                    continue
                errors = []
                for reader in c.readers:
                    try:
                        reader(self.ctx(c.name, cfg))
                    except Exception as e:
                        errors.append(_err(e))
                out[c.name] = {"readers": len(c.readers), "errors": errors}
        self.last_pass = {"at": now_iso(), "components": out}
        return self.last_pass

    def run_action(self, component: str, action: str, args: dict, apply: bool, cfg: dict) -> dict:
        c = next((c for c in self.components if c.name == component), None)
        if c is None or c.error or action not in c.actions:
            raise KeyError(f"{component}.{action}")
        plan = Plan(apply, f"{component}-{action}", self.claude_dir / "_archive", out=lambda _: None)
        ok, error = False, None
        try:
            ok = bool(c.actions[action](plan, self.ctx(component, cfg), **args))
        except Exception as e:
            error = _err(e)
            raise
        finally:
            path = plan.finish()
            if apply:
                row = {"at": now_iso(), "component": component, "action": action, "args": args,
                       "ok": ok, "manifest": str(path) if path else None, "error": error}
                self.home.mkdir(parents=True, exist_ok=True)
                with open(self.home / "actions.jsonl", "a") as f:
                    f.write(json.dumps(row) + "\n")
        return {"applied": apply, "ok": ok, "steps": plan.steps, "manifest": str(path) if path else None}

    def describe(self) -> dict:
        return {
            "components": [{"name": c.name, "readers": len(c.readers), "actions": sorted(c.actions),
                            "offers": sorted(c.offers), "reads": [list(r) for r in c.reads], "error": c.error}
                           for c in self.components],
            "store": str(self.home / "stellaxis.db"),
            "error": self.error,
            "last_pass": self.last_pass,
        }
