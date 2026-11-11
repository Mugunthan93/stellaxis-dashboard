# Stellaxis Core, Step 1 (Empty Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the core that runs readers, keeps one store, runs actions with undo and serves, with zero components and no change in what Stellaxis does today.

**Architecture:** A new stdlib package `collector/core/` holds the component contract, the SQLite store, the pass that runs readers, the undo log (moved out of `prune.py`) and the route handler. `collector/components/` is the folder the core loads components from; it stays empty in this step. `collect.py` calls the core once per collection pass and once per request, so the running service gains `GET /api/core` and loses nothing.

**Tech Stack:** Python 3 stdlib (`sqlite3`, `importlib`, `http.server`, `dataclasses`), `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-11-core-architecture-design.md` (words follow `CONTEXT.md`). This plan covers step 1 of its "Order of work" only. Steps 2 to 5 get their own plans.

## Global Constraints

- Python 3 standard library only. No packages, no build step.
- macOS, the launchd service `com.stellaxis.collector`, port 7777, 127.0.0.1 only.
- All existing tests pass after every task (176 today): `python3 -m unittest discover -s tests`.
- Every existing page, API route, the desktop MCP tool and the artifact path of `index.html` and `hierarchy.html` keep working after every task.
- Every change to files or settings is a dry run until applied. `prune.py` keeps the exact command line and the exact output it has today.
- The core starts and serves with zero components.
- The core depends on no component and on no file outside `collector/core/`. It never imports `collect`, `workspaces`, `prune` or `settings_api`.
- Every file in `collector/core/` starts with `from __future__ import annotations`, as the other collector files do.
- No new behaviour: no config key changes, no page changes, no renamed routes. The word "workspace" stays as it is until step 2.
- The shared transcript helper of the spec is not built here. It comes with step 2, which is its first user.

## Before starting

- The working tree holds uncommitted files: `CONTEXT.md`, the spec, this plan and an untracked `.claude/`. The owner decides whether to commit the three documents first.
- The service on port 7777 runs straight from this checkout. Work on a branch in a worktree, not in the checkout the service uses.
- Commit steps run only after the owner approves execution. End each commit message with the attribution line the session gives.
- Task 6 ends with a restart of the service. Do it only when the owner says so.

## Review Focus

1. A component folder that cannot be imported (a syntax error, a wrong name): the core still starts, and `/api/core` shows the error. Tests in Task 1 and Task 6.
2. A reader that raises: the other readers still run and the collection pass still finishes. Test in Task 3.
3. The store cannot be opened (the home folder is not writable, or the path is a file): the collection and every existing route still work, and the error shows in `status.json`. Tests in Task 3 and Task 6.
4. The collection loop and an HTTP request use the store at the same moment. Test in Task 2.
5. An archive that `prune.py` wrote before this change still restores after it. Test in Task 4.

## File Structure

| File | Responsibility |
|---|---|
| `collector/core/__init__.py` | Empty. Marks the package. |
| `collector/core/component.py` | The `Component` contract and `load()`, which imports every component folder. |
| `collector/core/store.py` | `Store`: the one SQLite file, connections, and the check that a component only creates its own tables. |
| `collector/core/app.py` | `Core` and `Ctx`: ties components to the store, runs readers, offers, reads and actions. |
| `collector/core/util.py` | `read_json`, `write_json`, `short_path`, moved from `collect.py`, plus `now_iso`. |
| `collector/core/undo.py` | `Plan`, the undo handlers and `restore`, moved from `prune.py`. |
| `collector/core/server.py` | `host_ok` (moved from `settings_api.py`), `handle()` for `/api/core` and component routes, `serve()` for the core on its own. |
| `collector/components/README.md` | Four lines: what a component folder must hold. No code. |
| `tests/test_core.py` | Every test of this plan, except one added to `tests/test_e2e.py`. |
| `collector/collect.py`, `collector/prune.py`, `collector/settings_api.py`, `CLAUDE.md` | Modified as each task says. |

All tests in `tests/test_core.py` start like the other test files:

```python
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "collector"))
from core import component, store, undo, server   # as each task adds them
from core.app import Core

def put(root: Path, name: str, init: str, **files: str) -> None:
    """Write a component folder: <root>/<name>/__init__.py plus <key>.py for each extra file."""
    d = root / name
    d.mkdir(parents=True)
    (d / "__init__.py").write_text(init)
    for stem, text in files.items():
        (d / f"{stem}.py").write_text(text)
```

Each test class makes a `tempfile.TemporaryDirectory()` in `setUp` and uses `self.home = Path(tmp)`, `self.comps = self.home / "components"`.

---

### Task 1: The component contract and the loader

**Files:**
- Create: `collector/core/__init__.py`, `collector/core/component.py`, `collector/components/README.md`
- Test: `tests/test_core.py`

**Interfaces:**
- Produces:

```python
@dataclass
class Component:
    name: str                                     # must match NAME and the folder name
    readers: list = field(default_factory=list)   # each: fn(ctx) -> None
    records: list = field(default_factory=list)   # SQL strings, see Task 2
    actions: dict = field(default_factory=dict)   # {name: fn(plan, ctx, **args) -> bool}
    offers: dict = field(default_factory=dict)    # {name: fn(ctx, **args) -> list}
    reads: list = field(default_factory=list)     # [(owner component name, offer name)]
    handle: Callable | None = None                # fn(handler, method, ctx) -> bool
    error: str | None = None

NAME = re.compile(r"[a-z][a-z0-9_]*")
def load(directory: Path) -> list[Component]
```

- [ ] **Step 1: Write the failing tests** (`class LoadTest`)

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest discover -s tests -k LoadTest -v`
Expected: FAIL, `No module named 'core'`

- [ ] **Step 3: Implement `Component`, `NAME` and `load` in `collector/core/component.py`**

A component is a subfolder of `directory` that has an `__init__.py` defining `COMPONENT`. Folders without `__init__.py` are skipped. Import each folder as a package named `stx_<folder>` with `importlib.util.spec_from_file_location(..., submodule_search_locations=[str(folder)])`, put it in `sys.modules` before running it (so relative imports work) and replace any earlier module of that name. Anything that goes wrong (an exception, `COMPONENT` missing or not a `Component`, a name that fails `NAME.fullmatch`, a name unlike the folder name) gives `Component(name=<folder name>, error="<ExceptionType>: <message>")` with every other field empty. `load` never raises. `README.md` in `collector/components/` says: one folder per component, `__init__.py` defines `COMPONENT`, the folder name is the component name, see the spec.

- [ ] **Step 4: Run them to see them pass**

Run: `python3 -m unittest discover -s tests -k LoadTest -v`
Expected: 4 tests OK

- [ ] **Step 5: Commit**

```bash
git add collector/core/__init__.py collector/core/component.py collector/components/README.md tests/test_core.py
git commit -m "Core: the component contract and the loader"
```

---

### Task 2: The store

**Files:**
- Create: `collector/core/store.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Produces:

```python
class Store:
    def __init__(self, path: Path)             # makes the parent folder and the file; journal_mode=WAL
    path: Path
    def connect(self) -> sqlite3.Connection    # a new connection on every call; row_factory=sqlite3.Row; timeout=5
    def ensure(self, name: str, records: list[str]) -> None
```

- [ ] **Step 1: Write the failing tests** (`class StoreTest`)

```python
def test_a_new_store_is_an_empty_database(self):
    s = store.Store(self.home / "deep" / "stellaxis.db")
    self.assertTrue(s.path.is_file())
    self.assertEqual(s.connect().execute("SELECT count(*) FROM sqlite_master").fetchone()[0], 0)

def test_ensure_creates_only_the_components_own_tables(self):
    s = store.Store(self.home / "stellaxis.db")
    own = ["CREATE TABLE IF NOT EXISTS alpha_things (id TEXT PRIMARY KEY)",
           "create index if not exists alpha_things_i ON alpha_things (id)"]
    s.ensure("alpha", own)
    s.ensure("alpha", own)
    with s.connect() as db:
        db.execute("INSERT INTO alpha_things VALUES ('a')")
    self.assertEqual(s.connect().execute("SELECT id FROM alpha_things").fetchone()["id"], "a")
    for bad in ("CREATE TABLE IF NOT EXISTS beta_things (id TEXT)", "CREATE TABLE alpha_more (id TEXT)",
                "DROP TABLE alpha_things", "CREATE TABLE IF NOT EXISTS alphathings (id TEXT)"):
        with self.assertRaises(ValueError):
            s.ensure("alpha", ["CREATE TABLE IF NOT EXISTS alpha_ok (id TEXT)", bad])
    names = {r["name"] for r in s.connect().execute("SELECT name FROM sqlite_master")}
    self.assertNotIn("alpha_ok", names)   # a rejected list creates nothing

def test_two_threads_use_the_store_at_once(self):
    s = store.Store(self.home / "stellaxis.db")
    s.ensure("alpha", ["CREATE TABLE IF NOT EXISTS alpha_n (n INTEGER)"])
    errors = []
    def write():
        try:
            for n in range(200):
                with s.connect() as db:
                    db.execute("INSERT INTO alpha_n VALUES (?)", (n,))
        except Exception as e:
            errors.append(e)
    t = threading.Thread(target=write)
    t.start()
    for _ in range(200):
        s.connect().execute("SELECT count(*) FROM alpha_n").fetchone()
    t.join()
    self.assertEqual(errors, [])
    self.assertEqual(s.connect().execute("SELECT count(*) FROM alpha_n").fetchone()[0], 200)
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest discover -s tests -k StoreTest -v`
Expected: FAIL, `cannot import name 'store'`

- [ ] **Step 3: Implement `Store` in `collector/core/store.py`**

`ensure` accepts a statement only when it matches, ignoring case, `CREATE TABLE IF NOT EXISTS <name>_…` or `CREATE INDEX IF NOT EXISTS <name>_…`. It checks the whole list first and raises `ValueError` naming the first bad statement before it runs any of them.

- [ ] **Step 4: Run them to see them pass**

Run: `python3 -m unittest discover -s tests -k StoreTest -v`
Expected: 3 tests OK

- [ ] **Step 5: Commit**

```bash
git add collector/core/store.py tests/test_core.py
git commit -m "Core: one SQLite store, each component creates only its own tables"
```

---

### Task 3: The core object: readers, offers and reads

**Files:**
- Create: `collector/core/app.py`, `collector/core/util.py`
- Modify: `collector/collect.py:118-137` (the three helpers move out)
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: `component.load`, `Component`, `Store` (Tasks 1 and 2).
- Produces:

```python
# core/util.py
def read_json(p: Path, default=None)          # moved unchanged from collect.py
def write_json(p: Path, data, indent=None)    # moved unchanged from collect.py
def short_path(p) -> str                      # moved unchanged from collect.py
def now_iso() -> str                          # UTC, "%Y-%m-%dT%H:%M:%SZ"

# core/app.py
class Ctx:
    name: str          # the component this context belongs to
    cfg: dict
    home: Path         # $STELLAXIS_HOME
    def db(self) -> sqlite3.Connection
    def read(self, owner: str, offer: str, **args) -> list

class Core:
    def __init__(self, home: Path, claude_dir: Path, components_dir: Path)
    home: Path; claude_dir: Path
    components: list[Component]
    store: Store | None
    error: str | None          # why the store could not be opened, else None
    last_pass: dict | None
    def ctx(self, name: str, cfg: dict) -> Ctx
    def run_pass(self, cfg: dict) -> dict
    def describe(self) -> dict
```

The store file is `home / "stellaxis.db"`.

- [ ] **Step 1: Write the failing tests** (`class CoreTest`)

```python
ALPHA = '''
from core.component import Component
def fill(ctx):
    with ctx.db() as db:
        db.execute("INSERT OR REPLACE INTO alpha_things VALUES (?)", (str(ctx.cfg.get("x")),))
def things(ctx, prefix=""):
    return [dict(r) for r in ctx.db().execute("SELECT id FROM alpha_things") if r["id"].startswith(prefix)]
COMPONENT = Component(name="alpha", records=["CREATE TABLE IF NOT EXISTS alpha_things (id TEXT PRIMARY KEY)"],
                      readers=[fill], offers={"things": things})
'''
BETA = '''
from core.component import Component
def bad(ctx):
    raise ValueError("bad")
def good(ctx):
    with ctx.db() as db:
        db.execute("INSERT INTO beta_seen VALUES (1)")
COMPONENT = Component(name="beta", records=["CREATE TABLE IF NOT EXISTS beta_seen (n INTEGER)"],
                      readers=[bad, good], reads=[("alpha", "things"), ("ghost", "things"), ("broken", "things")])
'''

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
    self.assertEqual(c.store.connect().execute("SELECT count(*) FROM beta_seen").fetchone()[0], 1)

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
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest discover -s tests -k CoreTest -v`
Expected: FAIL, `No module named 'core.app'`

- [ ] **Step 3: Move the three helpers into `collector/core/util.py`**

Cut `read_json`, `write_json` and `short_path` out of `collect.py` unchanged, add `now_iso`, and put `from core.util import read_json, write_json, short_path` among `collect.py`'s imports so `C.read_json` and the others still resolve for `prune.py`, `mcp_server.py` and the tests.

- [ ] **Step 4: Implement `Ctx` and `Core` in `collector/core/app.py`**

- `Core.__init__` never raises. It opens the store, loads the components, and calls `store.ensure(c.name, c.records)` for each component without an error; a `ValueError` there becomes that component's `error`. If the store cannot be opened, `error` is `"<ExceptionType>: <message>"`, `store` is `None`, and no component is run.
- `run_pass` runs every reader of every component that has no error, in name order, each with `ctx(name, cfg)`. An exception in one reader is recorded as `"<ExceptionType>: <message>"` and the next reader runs. It returns and stores in `last_pass`: `{"at": now_iso(), "components": {name: {"readers": <count>, "errors": [...]}}}`. Components with an error are left out.
- `Ctx.read(owner, offer, **args)` raises `LookupError` when `(owner, offer)` is not in the reading component's `reads`. It returns `[]` when the owner is absent, has an error, or has no such offer. Otherwise it returns `owner.offers[offer](ctx_of_owner, **args)`.
- `describe()` returns `{"components": [{"name", "readers": <count>, "actions": [sorted names], "offers": [sorted names], "reads": [[owner, offer]], "error"}], "store": str(path), "error", "last_pass"}`, components in name order, including those with an error.

- [ ] **Step 5: Run the new tests, then the whole suite**

Run: `python3 -m unittest discover -s tests -k CoreTest -v` then `python3 -m unittest discover -s tests`
Expected: 5 tests OK, then every test OK (the count is 176 plus the tests of Tasks 1 to 3)

- [ ] **Step 6: Commit**

```bash
git add collector/core/app.py collector/core/util.py collector/collect.py tests/test_core.py
git commit -m "Core: run readers, and let a component read only the offers it declared"
```

---

### Task 4: Undo moves into the core

**Files:**
- Create: `collector/core/undo.py`
- Modify: `collector/prune.py:47-76` (`stamp`, `Plan`), `collector/prune.py:100-107` (`dig`), `collector/prune.py:491-534` (`restore`)
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: `core.util` (Task 3).
- Produces:

```python
class RestoreError(Exception)
def dig(d: dict, keys: list, create=False)        # moved unchanged from prune.py
class Plan:
    def __init__(self, apply: bool, label: str, archive: Path, on_applied=None, undo_hint=None, out=print)
    apply: bool; steps: list[str]; dir: Path; manifest: dict   # dir = archive / f"{stamp}-{label}", as today
    def say(self, msg: str) -> None        # today's line through out(), and msg appended to steps
    def backup_json(self, path: Path) -> None
    def finish(self) -> Path | None        # the manifest path when one was written
HANDLERS: dict[str, Callable[[dict, bool, Callable], bool]]   # "move", "json_del", "json_add", "json_set": fn(entry, apply, out)
def restore(name: str, apply: bool, archive: Path, handlers: dict | None = None, on_applied=None, out=print) -> None
```

The manifest format does not change: `{"created", "label", "entries": [...]}` in `<archive>/<stamp>-<label>/manifest.json`, renamed to `manifest.restored.json` after a restore.

- [ ] **Step 1: Write the failing tests** (`class UndoTest`; `self.arch = self.home / ".claude" / "_archive"`)

```python
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
    kept.mkdir(parents=True); (kept / "SKILL.md").write_text("s")
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest discover -s tests -k UndoTest -v`
Expected: FAIL, `cannot import name 'undo'`

- [ ] **Step 3: Move the code into `collector/core/undo.py`**

Move `stamp`, `Plan`, `dig` and the body of `restore` out of `prune.py`. Every printed line keeps its exact text and goes through `out`. Where the code calls `C.collect()` today it calls `on_applied()` when one was given. Where it calls `sys.exit(msg)` it raises `RestoreError(msg)`. Each branch of today's `restore` loop becomes one function in `HANDLERS`; an entry whose op has no handler is skipped, as `json_backup` is today. Entries run in reverse order, as today.

- [ ] **Step 4: Make `prune.py` use it**

`prune.py` keeps the names its own code calls:

```python
def Plan(apply: bool, label: str) -> undo.Plan      # undo.Plan(apply, label, ARCHIVE, on_applied=C.collect, undo_hint=<today's "python3 … restore <name> --apply" text>)
def restore(name: str, apply: bool)                 # undo.restore(…, handlers={"relocate": <calls relocate(Plan(apply, "undo"), Path(e["to"]), Path(e["from"]), backups=False)>}, on_applied=C.collect); RestoreError -> sys.exit(str(e))
from core.undo import dig
```

- [ ] **Step 5: Run the new tests, then the whole suite**

Run: `python3 -m unittest discover -s tests -k UndoTest -v` then `python3 -m unittest discover -s tests`
Expected: 5 tests OK, then every test OK. `test_relocate` and `test_zz_prune_roundtrip` pass unchanged; they prove the command line and its output did not change.

- [ ] **Step 6: Commit**

```bash
git add collector/core/undo.py collector/prune.py tests/test_core.py
git commit -m "Core: undo and the archive manifest move out of prune.py"
```

---

### Task 5: Actions run through the core

**Files:**
- Modify: `collector/core/app.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: `undo.Plan` (Task 4), `Core`, `Ctx` (Task 3).
- Produces:

```python
# on Core
def run_action(self, component: str, action: str, args: dict, apply: bool, cfg: dict) -> dict
# returns {"applied": bool, "ok": bool, "steps": [str], "manifest": str | None}
```

Every applied action appends one line to `home / "actions.jsonl"`: `{"at", "component", "action", "args", "ok", "manifest", "error"}`.

- [ ] **Step 1: Write the failing tests** (`class ActionTest`)

```python
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
COMPONENT = Component(name="alpha", actions={"touch": touch})
'''

def setUp(self):   # after the temp folder
    put(self.comps, "alpha", ACT)
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

def test_unknown_names_are_key_errors(self):
    for comp, act in (("ghost", "touch"), ("alpha", "ghost")):
        with self.assertRaises(KeyError):
            self.core.run_action(comp, act, {}, False, {})
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest discover -s tests -k ActionTest -v`
Expected: FAIL, `'Core' object has no attribute 'run_action'`

- [ ] **Step 3: Implement `Core.run_action`**

It builds `undo.Plan(apply, f"{component}-{action}", self.claude_dir / "_archive", out=<discard>)`, calls `fn(plan, ctx, **args)`, then `plan.finish()`. When `apply` is true it appends the log line, also when the action raised: in that case it still calls `plan.finish()` so the steps already done can be undone, logs `ok: false` with `error: "<ExceptionType>: <message>"`, and re-raises. A component with an error, an unknown component or an unknown action is a `KeyError` before anything runs.

- [ ] **Step 4: Run them to see them pass**

Run: `python3 -m unittest discover -s tests -k ActionTest -v`
Expected: 4 tests OK

- [ ] **Step 5: Commit**

```bash
git add collector/core/app.py tests/test_core.py
git commit -m "Core: run a component's action as a dry run or applied, and log it"
```

---

### Task 6: Serve the core, and wire it into the service

**Files:**
- Create: `collector/core/server.py`
- Modify: `collector/settings_api.py:280-287` (`host_ok` moves out), `collector/collect.py` (imports, `collect()` near line 1300, `Handler.do_GET` and `do_POST` near lines 1355 and 1425), `CLAUDE.md` (Architecture section)
- Test: `tests/test_core.py`, `tests/test_e2e.py`

**Interfaces:**
- Consumes: `Core` (Tasks 3 and 5).
- Produces:

```python
# core/server.py
def host_ok(handler) -> bool                                        # moved unchanged from settings_api.py
def handle(core: Core, handler, method: str, cfg: dict) -> bool    # True when the request was answered
def serve(core: Core, port: int, cfg: dict) -> http.server.ThreadingHTTPServer   # bound to 127.0.0.1, not yet serving

# collect.py
def the_core() -> Core      # made on first use: Core(STX_HOME, CLAUDE_DIR, <collector>/components)
```

- [ ] **Step 1: Write the failing tests** (`class ServeTest`, `class WiringTest`, `class IndependenceTest`, and one in `tests/test_e2e.py`)

`ServeTest` starts `server.serve(core, 0, {})` in a thread and has a helper `get(path, method="GET", headers=None) -> (status, body bytes)` using `http.client`, as the other test files do.

```python
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

def test_the_core_serves_with_zero_components(self):          # ServeTest, core has no components
    status, body = self.get("/api/core")
    self.assertEqual(status, 200)
    self.assertEqual(json.loads(body), {"components": [], "store": str(self.home / "stellaxis.db"), "error": None, "last_pass": None})
    self.assertEqual(self.get("/anything")[0], 404)
    self.assertEqual(self.get("/api/core", method="POST")[0], 404)
    self.assertEqual(self.get("/api/core", headers={"Host": "evil.example"})[0], 403)

def test_component_routes_and_their_errors(self):             # ServeTest, components: WEB as "alpha", a broken "beta"
    self.assertEqual(self.get("/api/alpha"), (200, b"alpha"))
    status, body = self.get("/api/alpha/boom")
    self.assertEqual((status, json.loads(body)), (500, {"error": "RuntimeError: boom"}))
    self.assertEqual(self.get("/api/alpha")[0], 200)          # still serving
    by = {c["name"]: c for c in json.loads(self.get("/api/core")[1])["components"]}
    self.assertIn("RuntimeError", by["beta"]["error"])

def test_the_service_answers_api_core_and_keeps_its_routes(self):   # WiringTest: C.Handler on port 0
    core = Core(self.home, self.home / ".claude", self.home / "components")
    with mock.patch.object(C, "_core", core):
        self.assertEqual(json.loads(self.get("/api/core")[1])["components"], [])
        self.assertEqual(self.get("/api/core", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.get("/api/status")[0], 200)
        self.assertEqual(self.get("/overview")[0], 200)

def test_a_core_that_cannot_open_its_store_does_not_break_the_service(self):   # WiringTest
    (self.home / "stellaxis.db").mkdir()
    with mock.patch.object(C, "_core", Core(self.home, self.home / ".claude", self.home / "components")):
        self.assertTrue(json.loads(self.get("/api/core")[1])["error"])
        self.assertEqual(self.get("/api/status")[0], 200)

def test_host_ok_has_one_home(self):                          # WiringTest
    import settings_api
    self.assertIs(settings_api.host_ok, server.host_ok)

def test_the_core_imports_nothing_outside_itself(self):       # IndependenceTest
    outside = {p.stem for p in (REPO / "collector").glob("*.py")} | {"components"}
    for f in (REPO / "collector" / "core").glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            for n in names:
                self.assertNotIn(n.split(".")[0], outside, f"{f.name} imports {n}")
```

In `tests/test_e2e.py`, next to `test_status_file`:

```python
def test_core_store(self):
    stx = self.home / ".stellaxis"
    self.assertTrue((stx / "stellaxis.db").is_file())
    self.assertIsNone(json.loads((stx / "status.json").read_text())["core_error"])
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest discover -s tests -k ServeTest -k WiringTest -k IndependenceTest -k test_core_store -v`
Expected: FAIL, `cannot import name 'server'`

- [ ] **Step 3: Implement `collector/core/server.py`**

- `host_ok` is cut from `settings_api.py` unchanged; `settings_api.py` gets `from core.server import host_ok` so every existing caller keeps working.
- `handle` answers `GET /api/core` (path without the query string) with `core.describe()` as JSON, status 200, `Cache-Control: no-store`. For any other request it asks each component that has a `handle` and no error, in name order, with `ctx(name, cfg)`; the first that returns true ends it. If a component's `handle` raises, it answers 500 with JSON `{"error": "<ExceptionType>: <message>"}` and returns true. It does not check the host; the caller does.
- `serve` returns a `ThreadingHTTPServer` on `("127.0.0.1", port)` whose handler, for GET and POST, answers 403 when `host_ok` fails, calls `handle`, and answers 404 when that returns false. It logs nothing.

- [ ] **Step 4: Wire it into `collector/collect.py`**

- `the_core()` keeps its `Core` in the module global `_core` (initially `None`), so importing `collect` creates no file.
- In `collect()`, after `write_index`: call `the_core().run_pass(cfg)` and add `core_error=the_core().error` to the final `status("idle", …)` call.
- In `Handler.do_GET` and `Handler.do_POST`, right after the `host_ok` check and before `settings_api.handle`: `if core_server.handle(the_core(), self, <method>, load_config()): return`.

- [ ] **Step 5: Describe the core in `CLAUDE.md`**

Add to the Architecture list, in the file's own style: `collector/core/` (what each file does, one line each, from the File Structure table above), `collector/components/` (empty until step 2), the store file `stellaxis.db` and the log `actions.jsonl` under `$STELLAXIS_HOME`, the route `GET /api/core`, and the rule that the core imports nothing outside itself. Point to the spec for the rest.

- [ ] **Step 6: Run the new tests, then the whole suite**

Run: the command of Step 2, then `python3 -m unittest discover -s tests`
Expected: 7 tests OK, then every test OK.

- [ ] **Step 7: Commit**

```bash
git add collector/core/server.py collector/settings_api.py collector/collect.py CLAUDE.md tests/test_core.py tests/test_e2e.py
git commit -m "Core: serve /api/core, and run the core inside the service"
```

- [ ] **Step 8: Check the real service (only when the owner says so)**

```bash
launchctl kickstart -k gui/$(id -u)/com.stellaxis.collector
```

```bash
curl -s http://127.0.0.1:7777/api/core
```

Expected: `{"components": [], "store": "/Users/…/.stellaxis/stellaxis.db", "error": null, "last_pass": …}`, and the Overview page at `http://127.0.0.1:7777/overview` loads as before.

---

## What this plan leaves for later plans

- Step 2: Project management moves out of `workspaces.py`, the word "workspace" becomes "project", and the shared transcript helper is built.
- Step 3: Claude management moves out of `collect.py`, `prune.py` and `settings_api.py`.
- A generic HTTP route to run any action. Step 2 adds routes through its own component `handle`, so no such route is needed yet.
