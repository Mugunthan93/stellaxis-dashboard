"""Tests of the core package (collector/core) and its component loader.

  python3 -m unittest discover -s tests -k test_core
"""
import ast
import contextlib
import http.client
import http.server
import io
import json
import shutil
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "collector"))
import collect as C
from core import component, store, undo, server
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

    def test_a_component_folder_is_imported_once(self):
        put(self.comps, "alpha", "from core.component import Component\nSTATE = []\nCOMPONENT = Component(name='alpha')\n")
        m1 = component.import_folder(self.comps / "alpha")
        [c] = component.load(self.comps)
        m2 = component.import_folder(self.comps / "alpha")
        self.assertIs(m1, m2)
        self.assertIsNot(c, m1.COMPONENT)
        self.assertEqual(c.name, "alpha")
        self.assertIs(sys.modules["stx_alpha"], m1)

    def test_a_same_named_folder_elsewhere_replaces_it(self):
        other = self.home / "elsewhere"
        for root in (self.comps, other):
            put(root, "alpha", "from core.component import Component\nCOMPONENT = Component(name='alpha')\n")
        m1 = component.import_folder(self.comps / "alpha")
        m2 = component.import_folder(other / "alpha")
        self.assertIsNot(m1, m2)
        self.assertIs(sys.modules["stx_alpha"], m2)

    def test_a_failed_import_leaves_nothing_behind(self):
        put(self.comps, "beta", "from . import helper\nraise RuntimeError('boom')\n", helper="X = 1\n")
        [c] = component.load(self.comps)
        self.assertIn("RuntimeError: boom", c.error)
        self.assertEqual([k for k in sys.modules if k == "stx_beta" or k.startswith("stx_beta.")], [])

    def test_a_failed_import_can_be_fixed_and_loaded_again(self):
        put(self.comps, "beta", "raise RuntimeError('boom')\n")
        self.assertTrue(component.load(self.comps)[0].error)
        (self.comps / "beta" / "__init__.py").write_text("from core.component import Component\nCOMPONENT = Component(name='beta')\n")
        [c] = component.load(self.comps)
        self.assertIsNone(c.error)

    def test_threads_importing_one_folder_get_one_finished_module(self):
        put(self.comps, "slow", "import time\ntime.sleep(0.05)\nLOCK = object()\n")
        got = []
        def run():
            m = component.import_folder(self.comps / "slow")
            got.append((m, hasattr(m, "LOCK")))
        threads = [threading.Thread(target=run) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(got), 8)
        self.assertTrue(all(m is got[0][0] and ok for m, ok in got))


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
SEES = '''
from core.component import Component
SEEN = []
def look(ctx):
    SEEN.append(ctx.inputs)
COMPONENT = Component(name="sees", readers=[look])
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

    def test_records_that_fail_in_sqlite_stop_only_that_component(self):
        put(self.comps, "alpha", ALPHA)
        put(self.comps, "sick", "from core.component import Component\nCOMPONENT = Component(name='sick', records=['CREATE TABLE IF NOT EXISTS sick_t (id TEXT)', 'CREATE INDEX IF NOT EXISTS sick_i ON sick_t (missing_column)'])\n")
        c = self.core()
        by = {x["name"]: x for x in c.describe()["components"]}
        self.assertIn("OperationalError", by["sick"]["error"])
        self.assertIsNone(by["alpha"]["error"])
        self.assertEqual(set(c.run_pass({})["components"]), {"alpha"})

    def test_a_store_that_cannot_open_is_reported_not_raised(self):
        (self.home / "stellaxis.db").mkdir()
        put(self.comps, "alpha", ALPHA)
        c = self.core()
        self.assertTrue(c.error)
        self.assertIsNone(c.store)
        [e] = c.run_pass({})["components"]["alpha"]["errors"]
        self.assertTrue(e.startswith("StoreError: "))
        self.assertEqual(c.run_pass({})["components"], {"alpha": {"readers": 1, "errors": [e]}})
        self.assertEqual(c.describe()["error"], c.error)

    def test_a_core_never_writes_to_the_module_component(self):
        put(self.comps, "alpha", ALPHA)
        first = self.core()
        first.components[0].error = "ensure failed"
        self.home = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.home, True)
        second = Core(self.home, self.home / ".claude", self.comps)
        self.assertIsNone(second.components[0].error)
        self.assertEqual(set(second.run_pass({})["components"]), {"alpha"})
        self.assertIsNone(component.import_folder(self.comps / "alpha").COMPONENT.error)

    def test_readers_get_the_inputs_of_the_pass(self):
        put(self.comps, "sees", SEES)
        c = self.core()
        c.run_pass({}, {"sessions": [1, 2]})
        c.run_pass({})
        seen = component.import_folder(self.comps / "sees").SEEN
        self.assertEqual(seen, [{"sessions": [1, 2]}, {}])
        self.assertEqual(c.ctx("sees", {}).inputs, {})


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


class ActionTest(unittest.TestCase):
    ACT = '''
from pathlib import Path
from core.component import Component
def touch(plan, ctx, path, keep=False, fail=False):
    plan.say(f"create {path}")
    if keep:
        plan.manifest["entries"].append({"op": "move", "from": path + ".old", "to": path})
    if plan.apply:
        Path(path).write_text("x")
    if fail:
        raise RuntimeError("half way")
    return True
def refuse(plan, ctx):
    plan.say("refused")
    return False
COMPONENT = Component(name="alpha", actions={"touch": touch, "refuse": refuse})
'''

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.comps = self.home / "components"
        put(self.comps, "alpha", self.ACT)
        self.core = Core(self.home, self.home / ".claude", self.comps)
        self.f = str(self.home / "made.txt")
        self.log = self.home / "actions.jsonl"

    def test_a_dry_run_changes_nothing_and_prints_nothing(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = self.core.run_action("alpha", "touch", {"path": self.f}, False, {})
        self.assertEqual(r, {"applied": False, "ok": True, "steps": [f"create {self.f}"], "manifest": None})
        self.assertEqual((buf.getvalue(), Path(self.f).exists(), self.log.exists()), ("", False, False))

    def test_an_applied_action_is_logged(self):
        r = self.core.run_action("alpha", "touch", {"path": self.f, "keep": True}, True, {})
        self.assertTrue(Path(self.f).exists())
        m = Path(r["manifest"])
        self.assertEqual(m.parent.parent, self.home / ".claude" / "_archive")
        self.assertTrue(m.parent.name.endswith("-alpha-touch"))
        [row] = [json.loads(l) for l in self.log.read_text().splitlines()]
        self.assertEqual({k: row[k] for k in ("component", "action", "args", "ok", "manifest", "error")},
                         {"component": "alpha", "action": "touch", "args": {"path": self.f, "keep": True}, "ok": True, "manifest": r["manifest"], "error": None})

    def test_an_action_that_fails_half_way_still_leaves_its_manifest(self):
        with self.assertRaisesRegex(RuntimeError, "half way"):
            self.core.run_action("alpha", "touch", {"path": self.f, "keep": True, "fail": True}, True, {})
        [row] = [json.loads(l) for l in self.log.read_text().splitlines()]
        self.assertEqual((row["ok"], row["error"]), (False, "RuntimeError: half way"))
        self.assertTrue(Path(row["manifest"]).is_file())

    def test_an_action_that_returns_false_is_not_ok(self):
        r = self.core.run_action("alpha", "refuse", {}, False, {})
        self.assertEqual(r, {"applied": False, "ok": False, "steps": ["refused"], "manifest": None})
        self.assertFalse(self.log.exists())
        r = self.core.run_action("alpha", "refuse", {}, True, {})
        self.assertEqual((r["applied"], r["ok"]), (True, False))
        [row] = [json.loads(l) for l in self.log.read_text().splitlines()]
        self.assertEqual((row["ok"], row["error"]), (False, None))

    def test_unknown_names_are_key_errors(self):
        for comp, act in (("ghost", "touch"), ("alpha", "ghost")):
            with self.assertRaises(KeyError):
                self.core.run_action(comp, act, {}, False, {})


WEB = '''
from core.component import Component
def handle(handler, method, ctx):
    if handler.path == "/api/alpha/boom":
        raise RuntimeError("boom")
    if handler.path != "/api/alpha" or method != "GET":
        return False
    handler.send_response(200); handler.end_headers(); handler.wfile.write(ctx.name.encode())
    return True
COMPONENT = Component(name="alpha", handle=handle)
'''


class HttpCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.comps = self.home / "components"

    def start(self, srv):
        self.srv = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

    def get(self, path, method="GET", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        with closing(conn):
            conn.request(method, path, headers=headers or {})
            r = conn.getresponse()
            return r.status, r.read()


class ServeTest(HttpCase):
    def serve(self):
        self.start(server.serve(Core(self.home, self.home / ".claude", self.comps), 0, {}))

    def test_the_core_serves_with_zero_components(self):
        self.serve()
        status, body = self.get("/api/core")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"components": [], "store": str(self.home / "stellaxis.db"), "error": None, "last_pass": None})
        self.assertEqual(self.get("/anything")[0], 404)
        self.assertEqual(self.get("/api/core", method="POST")[0], 404)
        self.assertEqual(self.get("/api/core", headers={"Host": "evil.example"})[0], 403)

    def test_component_routes_and_their_errors(self):
        put(self.comps, "alpha", WEB)
        put(self.comps, "beta", "raise RuntimeError('broken')")
        self.serve()
        self.assertEqual(self.get("/api/alpha"), (200, b"alpha"))
        status, body = self.get("/api/alpha/boom")
        self.assertEqual((status, json.loads(body)), (500, {"error": "RuntimeError: boom"}))
        self.assertEqual(self.get("/api/alpha")[0], 200)
        by = {c["name"]: c for c in json.loads(self.get("/api/core")[1])["components"]}
        self.assertIn("RuntimeError", by["beta"]["error"])

    def test_components_still_answer_without_a_store(self):
        put(self.comps, "alpha", WEB)
        (self.home / "stellaxis.db").mkdir()
        self.serve()
        self.assertTrue(json.loads(self.get("/api/core")[1])["error"])
        self.assertEqual(self.get("/api/alpha")[0], 200)


class WiringTest(HttpCase):
    def setUp(self):
        super().setUp()
        self.start(http.server.ThreadingHTTPServer(("127.0.0.1", 0), C.Handler))

    def test_the_service_answers_api_core_and_keeps_its_routes(self):
        core = Core(self.home, self.home / ".claude", self.home / "components")
        with mock.patch.object(C, "_core", core):
            self.assertEqual(json.loads(self.get("/api/core")[1])["components"], [])
            self.assertEqual(self.get("/api/core", headers={"Host": "evil.example"})[0], 403)
            self.assertEqual(self.get("/api/status")[0], 200)
            self.assertEqual(self.get("/overview")[0], 200)

    def test_a_core_that_cannot_open_its_store_does_not_break_the_service(self):
        (self.home / "stellaxis.db").mkdir()
        with mock.patch.object(C, "_core", Core(self.home, self.home / ".claude", self.home / "components")):
            self.assertTrue(json.loads(self.get("/api/core")[1])["error"])
            self.assertEqual(self.get("/api/status")[0], 200)

    def test_the_first_build_of_the_core_is_locked(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        got = []
        with mock.patch.object(C, "_core", None), mock.patch.object(C, "STX_HOME", home / "stx"), \
                mock.patch.object(C, "CLAUDE_DIR", home / "claude"):
            threads = [threading.Thread(target=lambda: got.append(C.the_core())) for _ in range(8)]
            for t in threads: t.start()
            for t in threads: t.join()
        self.assertEqual(len(got), 8)
        self.assertTrue(all(g is got[0] for g in got))
        self.assertIsNone(got[0].error)

    def test_host_ok_has_one_home(self):
        import settings_api
        self.assertIs(settings_api.host_ok, server.host_ok)


class IndependenceTest(unittest.TestCase):
    def test_the_core_imports_nothing_outside_itself(self):
        outside = {p.stem for p in (REPO / "collector").glob("*.py")} | {"components"}
        for f in (REPO / "collector" / "core").glob("*.py"):
            for node in ast.walk(ast.parse(f.read_text())):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
                for n in names:
                    self.assertNotIn(n.split(".")[0], outside, f"{f.name} imports {n}")


if __name__ == "__main__":
    unittest.main()
