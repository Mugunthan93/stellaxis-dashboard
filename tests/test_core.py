"""Tests of the core package (collector/core) and its component loader.

  python3 -m unittest discover -s tests -k test_core
"""
import json
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "collector"))
from core import component, store, undo
from core.app import Core


def put(root: Path, name: str, init: str, **files: str) -> None:
    """Write a component folder: <root>/<name>/__init__.py plus <key>.py for each extra file."""
    d = root / name
    d.mkdir(parents=True)
    (d / "__init__.py").write_text(init)
    for stem, text in files.items():
        (d / f"{stem}.py").write_text(text)


class LoadTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.comps = self.home / "components"

    def test_missing_or_empty_folder_gives_no_components(self):
        self.assertEqual(component.load(self.comps / "absent"), [])
        self.comps.mkdir()
        (self.comps / "__pycache__").mkdir()
        self.assertEqual(component.load(self.comps), [])

    def test_loads_components_in_name_order(self):
        for n in ("beta", "alpha"):
            put(self.comps, n, f"from core.component import Component\nCOMPONENT = Component(name='{n}')\n")
        a, b = component.load(self.comps)
        self.assertEqual((a.name, a.error, a.readers, a.actions, a.offers, a.reads, a.handle), ("alpha", None, [], {}, {}, [], None))
        self.assertEqual(b.name, "beta")

    def test_a_component_can_import_its_own_files(self):
        put(self.comps, "alpha", "from core.component import Component\nfrom .readers import r\nCOMPONENT = Component(name='alpha', readers=[r])\n",
            readers="def r(ctx):\n    pass\n")
        [c] = component.load(self.comps)
        self.assertEqual((c.error, len(c.readers)), (None, 1))

    def test_a_broken_component_is_reported_not_raised(self):
        ok = "from core.component import Component\nCOMPONENT = Component(name='{}')\n"
        put(self.comps, "beta", "raise RuntimeError('boom')\n")
        put(self.comps, "gamma", "COMPONENT = 1\n")
        put(self.comps, "delta", ok.format("other"))
        put(self.comps, "Bad-Name", ok.format("Bad-Name"))
        put(self.comps, "alpha", ok.format("alpha"))
        by = {c.name: c for c in component.load(self.comps)}
        self.assertEqual(set(by), {"alpha", "beta", "gamma", "delta", "Bad-Name"})
        self.assertIsNone(by["alpha"].error)
        self.assertIn("RuntimeError: boom", by["beta"].error)
        for n in ("gamma", "delta", "Bad-Name"):
            self.assertTrue(by[n].error)
            self.assertEqual((by[n].readers, by[n].actions, by[n].offers), ([], {}, {}))


class StoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def q(self, s, sql):
        with closing(s.connect()) as db:
            return db.execute(sql).fetchall()

    def test_a_new_store_is_an_empty_database(self):
        s = store.Store(self.home / "deep" / "stellaxis.db")
        self.assertTrue(s.path.is_file())
        self.assertEqual(self.q(s, "SELECT count(*) FROM sqlite_master")[0][0], 0)

    def test_ensure_creates_only_the_components_own_tables(self):
        s = store.Store(self.home / "stellaxis.db")
        own = ["CREATE TABLE IF NOT EXISTS alpha_things (id TEXT PRIMARY KEY)",
               "create index if not exists alpha_things_i ON alpha_things (id)"]
        s.ensure("alpha", own)
        s.ensure("alpha", own)
        with closing(s.connect()) as db, db:
            db.execute("INSERT INTO alpha_things VALUES ('a')")
        self.assertEqual(self.q(s, "SELECT id FROM alpha_things")[0]["id"], "a")
        for bad in ("CREATE TABLE IF NOT EXISTS beta_things (id TEXT)", "CREATE TABLE alpha_more (id TEXT)",
                    "DROP TABLE alpha_things", "CREATE TABLE IF NOT EXISTS alphathings (id TEXT)"):
            with self.assertRaises(ValueError):
                s.ensure("alpha", ["CREATE TABLE IF NOT EXISTS alpha_ok (id TEXT)", bad])
        names = {r["name"] for r in self.q(s, "SELECT name FROM sqlite_master")}
        self.assertNotIn("alpha_ok", names)   # a rejected list creates nothing

    def test_two_threads_use_the_store_at_once(self):
        s = store.Store(self.home / "stellaxis.db")
        s.ensure("alpha", ["CREATE TABLE IF NOT EXISTS alpha_n (n INTEGER)"])
        errors = []

        def write():
            try:
                for n in range(200):
                    with closing(s.connect()) as db, db:
                        db.execute("INSERT INTO alpha_n VALUES (?)", (n,))
            except Exception as e:
                errors.append(e)
        t = threading.Thread(target=write)
        t.start()
        for _ in range(200):
            self.q(s, "SELECT count(*) FROM alpha_n")
        t.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.q(s, "SELECT count(*) FROM alpha_n")[0][0], 200)


ALPHA = '''
from contextlib import closing
from core.component import Component
def fill(ctx):
    with closing(ctx.db()) as db, db:
        db.execute("INSERT OR REPLACE INTO alpha_things VALUES (?)", (str(ctx.cfg.get("x")),))
def things(ctx, prefix=""):
    with closing(ctx.db()) as db:
        return [dict(r) for r in db.execute("SELECT id FROM alpha_things") if r["id"].startswith(prefix)]
COMPONENT = Component(name="alpha", records=["CREATE TABLE IF NOT EXISTS alpha_things (id TEXT PRIMARY KEY)"],
                      readers=[fill], offers={"things": things})
'''
BETA = '''
from contextlib import closing
from core.component import Component
def bad(ctx):
    raise ValueError("bad")
def good(ctx):
    with closing(ctx.db()) as db, db:
        db.execute("INSERT INTO beta_seen VALUES (1)")
COMPONENT = Component(name="beta", records=["CREATE TABLE IF NOT EXISTS beta_seen (n INTEGER)"],
                      readers=[bad, good], reads=[("alpha", "things"), ("ghost", "things"), ("broken", "things")])
'''


class CoreTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.comps = self.home / "components"

    def core(self):
        return Core(self.home, self.home / ".claude", self.comps)

    def test_starts_with_zero_components(self):
        c = self.core()
        self.assertEqual((c.components, c.error, c.last_pass), ([], None, None))
        self.assertTrue((self.home / "stellaxis.db").is_file())
        r = c.run_pass({})
        self.assertEqual(r["components"], {})
        self.assertRegex(r["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(c.last_pass, r)
        self.assertEqual(c.describe(), {"components": [], "store": str(self.home / "stellaxis.db"), "error": None, "last_pass": r})

    def test_a_reader_that_raises_does_not_stop_the_others(self):
        put(self.comps, "alpha", ALPHA); put(self.comps, "beta", BETA)
        put(self.comps, "broken", "raise RuntimeError('boom')\n")
        c = self.core()
        r = c.run_pass({"x": 7})
        self.assertEqual(r["components"], {"alpha": {"readers": 1, "errors": []}, "beta": {"readers": 2, "errors": ["ValueError: bad"]}})
        ctx = c.ctx("alpha", {"k": 1})
        self.assertEqual((ctx.name, ctx.cfg, ctx.home), ("alpha", {"k": 1}, self.home))
        with closing(c.store.connect()) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM beta_seen").fetchone()[0], 1)

    def test_reads_go_through_declared_offers_only(self):
        put(self.comps, "alpha", ALPHA); put(self.comps, "beta", BETA)
        put(self.comps, "broken", "raise RuntimeError('boom')\n")
        c = self.core(); c.run_pass({"x": 7})
        beta = c.ctx("beta", {})
        self.assertEqual(beta.read("alpha", "things"), [{"id": "7"}])
        self.assertEqual(beta.read("alpha", "things", prefix="z"), [])
        self.assertEqual(beta.read("ghost", "things"), [])      # the owner is not installed
        self.assertEqual(beta.read("broken", "things"), [])     # the owner failed to load
        with self.assertRaises(LookupError):
            beta.read("alpha", "other")                         # never declared

    def test_bad_records_stop_only_that_component(self):
        put(self.comps, "alpha", ALPHA)
        put(self.comps, "thief", "from core.component import Component\nCOMPONENT = Component(name='thief', records=['CREATE TABLE IF NOT EXISTS alpha_x (id TEXT)'])\n")
        c = self.core()
        by = {x["name"]: x for x in c.describe()["components"]}
        self.assertIn("ValueError", by["thief"]["error"])
        self.assertEqual(by["alpha"], {"name": "alpha", "readers": 1, "actions": [], "offers": ["things"], "reads": [], "error": None})
        self.assertEqual(set(c.run_pass({})["components"]), {"alpha"})

    def test_a_store_that_cannot_open_is_reported_not_raised(self):
        (self.home / "stellaxis.db").mkdir()
        put(self.comps, "alpha", ALPHA)
        c = self.core()
        self.assertTrue(c.error)
        self.assertIsNone(c.store)
        self.assertEqual(c.run_pass({})["components"], {})
        self.assertEqual(c.describe()["error"], c.error)


class UndoTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.arch = self.home / ".claude" / "_archive"

    def test_a_dry_run_plan_writes_nothing(self):
        lines = []
        p = undo.Plan(False, "t", self.arch, out=lines.append)
        p.say("move a -> b")
        p.manifest["entries"].append({"op": "move", "from": "a", "to": "b"})
        self.assertIsNone(p.finish())
        self.assertFalse(self.arch.exists())
        self.assertEqual(p.steps, ["move a -> b"])
        self.assertEqual(lines, ["  [dry-run] move a -> b", "\nNothing changed. Re-run with --apply to execute."])

    def test_an_applied_plan_writes_the_manifest_and_calls_back(self):
        lines, called = [], []
        p = undo.Plan(True, "t", self.arch, on_applied=lambda: called.append(1), undo_hint=lambda n: f"undo {n}", out=lines.append)
        p.manifest["entries"].append({"op": "move", "from": "a", "to": "b"})
        m = p.finish()
        self.assertEqual(m, p.dir / "manifest.json")
        self.assertEqual(json.loads(m.read_text())["entries"], [{"op": "move", "from": "a", "to": "b"}])
        self.assertEqual(called, [1])
        self.assertIn(f"Undo:     undo {p.dir.name}", lines)

    def test_an_applied_plan_with_no_entries_writes_nothing(self):
        self.assertIsNone(undo.Plan(True, "t", self.arch, out=lambda _: None).finish())
        self.assertFalse(self.arch.exists())

    def old_archive(self):
        """A manifest exactly as prune.py wrote it before this change, with one entry of every kind."""
        d = self.arch / "20261101-090000-archive"
        kept, cfg = d / "files" / "skill", self.home / "cfg.json"
        kept.mkdir(parents=True)
        (kept / "SKILL.md").write_text("s")
        cfg.write_text(json.dumps({"mcpServers": {"added": {}}, "enabledPlugins": {"p": False}}))
        (d / "manifest.json").write_text(json.dumps({"created": "2026-11-01T09:00:00Z", "label": "archive", "entries": [
            {"op": "json_backup", "backup_of": str(cfg), "backup": str(d / "json-backups" / "x")},
            {"op": "move", "id": "skill:user:s", "from": str(self.home / "skills" / "s"), "to": str(kept)},
            {"op": "json_del", "id": "mcp:user:gone", "file": str(cfg), "keys": ["mcpServers"], "name": "gone", "value": {"command": "x"}},
            {"op": "json_add", "id": "mcp:user:added", "file": str(cfg), "keys": ["mcpServers"], "name": "added"},
            {"op": "json_set", "id": "plugin:user:p", "file": str(cfg), "keys": ["enabledPlugins"], "name": "p", "prev": None},
            {"op": "future_op", "x": 1}]}))
        return d, cfg

    def test_an_old_archive_still_restores(self):
        d, cfg = self.old_archive()
        lines = []
        undo.restore("last", False, self.arch, out=lines.append)
        self.assertTrue((d / "manifest.json").exists())
        self.assertFalse((self.home / "skills" / "s").exists())
        self.assertEqual(lines[0], f"Restoring {d.name}:")
        self.assertTrue(all(l.startswith("  [dry-run] ") for l in lines[1:-1]))
        called = []
        undo.restore(d.name, True, self.arch, on_applied=lambda: called.append(1), out=lambda _: None)
        self.assertEqual((self.home / "skills" / "s" / "SKILL.md").read_text(), "s")
        self.assertEqual(json.loads(cfg.read_text()), {"mcpServers": {"gone": {"command": "x"}}, "enabledPlugins": {}})
        self.assertTrue((d / "manifest.restored.json").exists())
        self.assertEqual(called, [1])

    def test_restore_stops_when_a_handler_says_no(self):
        d, _ = self.old_archive()
        with self.assertRaisesRegex(undo.RestoreError, "restore stopped; nothing after this step was changed"):
            undo.restore(d.name, True, self.arch, handlers={"future_op": lambda e, apply, out: False}, out=lambda _: None)
        self.assertTrue((d / "manifest.json").exists())
        with self.assertRaisesRegex(undo.RestoreError, "no manifest in"):
            undo.restore("absent", True, self.arch)


if __name__ == "__main__":
    unittest.main()
