# Stellaxis Core, Step 2A (Project Management Becomes the First Component) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the command centre code out of `collector/workspaces.py` into the first component, `project_management`, served and run through the core, and move the Jarvis code apart, with no change in what Stellaxis does.

**Architecture:** The file `collector/workspaces.py` moves unchanged to `collector/components/project_management/centre.py`. The component's `__init__.py` gives the core one reader (it writes the index that `collect.py` writes today) and one `handle` (today's routes). `collector/workspaces.py` stays as a three-line alias that is the very same module object, so every existing import and test keeps working. The Jarvis functions move to `collector/unplaced/jarvis.py`.

**Tech Stack:** Python 3 stdlib, `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-11-core-architecture-design.md` (words follow `CONTEXT.md`). This plan is the first half of step 2 of its "Order of work". The second half (step 2B) renames "workspace" to "project".

## Global Constraints

- Python 3 standard library only. No packages, no build step.
- macOS, the launchd service `com.stellaxis.collector`, port 7777, 127.0.0.1 only.
- All existing tests pass after every task (208 before Task 1): `python3 -m unittest discover -s tests`.
- Every existing page, API route, the desktop MCP tool and the artifact path of `index.html` and `hierarchy.html` keep working after every task.
- No renamed route, JSON key, file, config key or page text in this plan. The word "workspace" stays everywhere until step 2B.
- `import workspaces` keeps working, and it is the same module object as the component's `centre` module: one copy of every lock and cache.
- `tests/test_workspaces.py` is not edited, except that Task 3 moves the Jarvis tests out of it unchanged in what they assert.
- The core depends on no component and on no file outside `collector/core/`. Every file in `collector/core/` starts with `from __future__ import annotations`.
- Never use `git stash`. Never run `launchctl` or `collect.py serve`. Never touch port 7777. Work only in the worktree.

## Decisions made by the writer (my calls)

- **Step 2 is two plans.** This one moves the code (2A). The next one renames "workspace" to "project" in routes, pages, config and the Jarvis agent (2B). Each leaves a working service.
- **The move is verbatim, in one file.** The tests patch names on the module (`W.TEST_TIMEOUT`, `"workspaces.subprocess.run"`, `C.workspaces.live`). Splitting the code into several files would break those patches, so the 800 lines stay together for now.
- **The index stays a JSON file here.** Records in the store, the shared transcript helper, and `relocate` as an action of this component move to the step 3 plan, where `collect.py` and `prune.py` are taken apart. The step 1 plan said the transcript helper comes with step 2; in step 2 the only transcript parser is still `collect.py`'s, so a helper would have one user.
- **With the store down, components still run.** This replaces the step 1 ruling "no component runs without a store". The command centre does not use the store yet and must keep answering. Whatever calls `ctx.db()` fails alone.
- **Jarvis still imports the command centre module directly** (`import workspaces as W`) for `_home`, `_bin`, `_prompt`, `_index`, `live` and `pending`. That ends when the ways in get their own design.
- **Waits for step 2B, because nothing in 2A needs it:** `Ctx.act`, the unique manifest folder, undo steps owned by a component, `restore` refusing an unknown step, the "POSTs need `application/json`" rule in the core, a schema-change rule.

## Before starting

- Branch `core-architecture` is local and not pushed. Work on a new branch in a worktree; the service runs from the main checkout.
- Commit steps run only after the owner approves execution. End each commit message with the attribution line the session gives.
- Task 3 ends with a restart of the service. Do it only when the owner says so.

## Review Focus

1. The component is imported a second time under another name, which would give two copies of `_TASK_LOCK`, `_RUN_LOCK`, `_RUNNING` and `_STATE_CACHE`. Test in Task 2.
2. The store cannot be opened: the command centre routes and the index must keep working. Tests in Task 1 and Task 2.
3. The component fails to load: `status.json` must say so in `index_error`, and every other page and route must keep answering. Test in Task 2.
4. One test class run alone (`-k Service`) builds the real core in the real `~/.stellaxis`. Fix in Task 2.
5. After the Jarvis move, its routes still refuse a foreign Host and still allow one question at a time. Tests in Task 3 (the moved tests, and `RouteMatrix`, which must pass unedited).

## File Structure

| File | Responsibility |
|---|---|
| `collector/core/component.py` | Adds `import_folder`: a component folder is imported once, under one name. |
| `collector/core/app.py` | Adds `StoreError`, the pass inputs (`run_pass(cfg, inputs)`, `Ctx.inputs`), and the store-down rule. |
| `collector/core/server.py` | Component routes are offered also when the store is down. |
| `collector/components/project_management/__init__.py` | `COMPONENT`: the index reader and the route handler. Nothing else. |
| `collector/components/project_management/centre.py` | The command centre code, moved unchanged from `collector/workspaces.py`. |
| `collector/workspaces.py` | Alias: makes `import workspaces` return `centre`. |
| `collector/unplaced/__init__.py`, `collector/unplaced/jarvis.py` | Code that runs today and is in no component yet. Jarvis: briefing, ask, hear, say, hush. |
| `tests/test_project_management.py` | Tests of Task 2. |
| `tests/test_jarvis.py` | The Jarvis tests, moved from `tests/test_workspaces.py` in Task 3. |

---

### Task 1: The core contract the first component needs

**Files:**
- Modify: `collector/core/component.py`, `collector/core/app.py`, `collector/core/server.py`, `collector/components/README.md`, `CLAUDE.md`
- Test: `tests/test_core.py`

**Interfaces:**
- Produces:

```python
# core/component.py
def import_folder(folder: Path) -> types.ModuleType
# The package of a component folder, imported as "stx_<folder name>".
# Same folder again -> the same module object. A folder of the same name at another path -> imported anew, replacing it.
# An import that raises leaves no "stx_<name>" entry (or submodule entry) in sys.modules, and re-raises.

# core/app.py
class StoreError(RuntimeError)
class Ctx:
    inputs: dict                      # what the host handed to this pass; {} outside a pass
    def db(self)                      # raises StoreError(<Core.error text>) when the store could not be opened
class Core:
    def ctx(self, name: str, cfg: dict, inputs: dict | None = None) -> Ctx
    def run_pass(self, cfg: dict, inputs: dict | None = None) -> dict
```

- [ ] **Step 1: Write the failing tests**

Add to `LoadTest`:

```python
def test_a_component_folder_is_imported_once(self):
    put(self.comps, "alpha", "from core.component import Component\nSTATE = []\nCOMPONENT = Component(name='alpha')\n")
    m1 = component.import_folder(self.comps / "alpha")
    [c] = component.load(self.comps)
    m2 = component.import_folder(self.comps / "alpha")
    self.assertIs(m1, m2)
    self.assertIs(c, m1.COMPONENT)
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
```

Add to `CoreTest` (it uses the `ALPHA` fake component and the `put` helper already in the file):

```python
SEES = '''
from core.component import Component
SEEN = []
def look(ctx):
    SEEN.append(ctx.inputs)
COMPONENT = Component(name="sees", readers=[look])
'''

def test_readers_get_the_inputs_of_the_pass(self):
    put(self.comps, "sees", SEES)
    c = self.core()
    c.run_pass({}, {"sessions": [1, 2]})
    c.run_pass({})
    seen = component.import_folder(self.comps / "sees").SEEN
    self.assertEqual(seen, [{"sessions": [1, 2]}, {}])
    self.assertEqual(c.ctx("sees", {}).inputs, {})
```

Change two existing tests to the new store-down rule, keeping their set-up:

- `CoreTest.test_a_store_that_cannot_open_is_reported_not_raised`: `Core(...)` still does not raise, `c.error` is set and `c.store` is `None`. The pass now runs the reader: `c.run_pass({})["components"]` equals `{"alpha": {"readers": 1, "errors": [e]}}` where `e` starts with `"StoreError: "`.
- `ServeTest.test_no_component_runs_without_a_store` becomes `test_components_still_answer_without_a_store`: with the store path made a folder, `GET /api/alpha` of the `WEB` fake component answers 200, and `GET /api/core` still shows the store error.

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest tests.test_core.LoadTest tests.test_core.CoreTest tests.test_core.ServeTest -v`
Expected: the new and changed tests FAIL (`import_folder` missing, `run_pass() takes 2 positional arguments`, the old store-down expectations).

- [ ] **Step 3: Implement**

- `import_folder`: the body of today's `_import` up to `exec_module`, with the reuse rule: when `sys.modules` already holds `stx_<name>` and that module's `__file__` resolves to this folder's `__init__.py`, return it. `_import` calls `import_folder` and then checks `COMPONENT` as today.
- `Core.run_pass` no longer skips everything when `store` is `None`; `core/server.py` `handle` no longer returns early when `core.error` is set. `Ctx.db()` raises `StoreError`.
- `inputs` reaches readers only: `run_pass` builds each reader's context with it; every other context has `{}`.
- `collector/components/README.md`: add three lines. A connection from `ctx.db()` is the caller's to close. An action returns True on success. In tests, get a component's module with `core.component.import_folder(folder)` and never import it by another name.
- `CLAUDE.md`: replace the sentence that says no component runs without the store with the new rule.

- [ ] **Step 4: Run the changed classes, then the whole suite**

Run: `python3 -W error -X dev -m unittest tests.test_core.LoadTest tests.test_core.CoreTest tests.test_core.ServeTest` then `python3 -m unittest discover -s tests`
Expected: all OK with clean output, then 212 tests OK.

- [ ] **Step 5: Commit**

```bash
git add collector/core collector/components/README.md CLAUDE.md tests/test_core.py
git commit -m "Core: one import name per component, pass inputs for readers, components run with the store down"
```

---

### Task 2: The command centre moves into the component

**Files:**
- Move: `collector/workspaces.py` to `collector/components/project_management/centre.py` (`git mv`, content unchanged)
- Create: `collector/components/project_management/__init__.py`, `collector/workspaces.py` (the alias), `tests/test_project_management.py`
- Modify: `collector/collect.py` (`write_index`, `collect()`, `Handler.do_GET`, `Handler.do_POST`), `tests/test_hardening.py` (classes `Service` and `RouteMatrix`), `CLAUDE.md`

**Interfaces:**
- Consumes: `import_folder`, `Ctx.inputs`, `Core.run_pass(cfg, inputs)` (Task 1).
- Produces:

```python
# collector/components/project_management/__init__.py
def index_reader(ctx) -> None
def handle(handler, method: str, ctx) -> bool
COMPONENT = Component(name="project_management", readers=[index_reader], handle=handle)

# collector/collect.py (same name and return type as today)
def write_index(cfg: dict, snap: dict) -> str | None
```

The inputs of the pass are `{"sessions": [<session rows>], "folders": [<snap["projects"] rows>]}`.

- [ ] **Step 1: Write the failing tests** (`tests/test_project_management.py`)

The file starts like `tests/test_core.py` (`REPO`, `sys.path.insert(0, str(REPO / "collector"))`) and imports `from core import component, server`, `from core.app import Core`, `import collect as C`, `import workspaces as W`. `COMPS = REPO / "collector" / "components"`. `setUp` makes a temp folder `self.home`, sets the environment variables `STELLAXIS_HOME=self.home` and `CLAUDE_CONFIG_DIR=self.home/".claude"` with `mock.patch.dict(os.environ, …)`, and builds `self.core = Core(self.home, self.home / ".claude", COMPS)`. `CFG = {"workspaces": [], "claude_bin": "/no/claude"}`. `ROW = {"id": "s1", "project": "~/nowhere/x", "first_ts": "2026-11-01T00:00:00Z", "last_ts": "2026-11-01T00:00:00Z", "cost": 1.5, "branch": "main"}`.

```python
def test_the_component_loads(self):
    [c] = [c for c in self.core.components if c.name == "project_management"]
    self.assertEqual((c.error, len(c.readers), c.handle is not None), (None, 1, True))

def test_the_old_name_is_the_same_module(self):
    centre = component.import_folder(COMPS / "project_management").centre
    self.assertIs(W, centre)
    self.assertIs(sys.modules["workspaces"], centre)
    self.assertIs(C.workspaces, centre)
    self.assertIs(W._TASK_LOCK, centre._TASK_LOCK)

def test_a_pass_writes_the_index_from_its_inputs(self):
    with mock.patch.object(W, "live", return_value=[]), mock.patch.object(W, "app_records", return_value={}):
        res = self.core.run_pass(dict(CFG), {"sessions": [ROW], "folders": []})
    self.assertEqual(res["components"]["project_management"], {"readers": 1, "errors": []})
    idx = json.loads((self.home / "workspaces.json").read_text())
    self.assertEqual([w["id"] for w in idx["workspaces"]], ["other"])
    self.assertEqual(idx["workspaces"][0]["repos"][0]["branches"][0]["sessions"][0]["id"], "s1")

def test_a_pass_without_inputs_leaves_the_index_alone(self):
    res = self.core.run_pass(dict(CFG))
    self.assertEqual(res["components"]["project_management"]["errors"], [])
    self.assertFalse((self.home / "workspaces.json").exists())

def test_routes_are_served_through_the_core_also_with_the_store_down(self):
    (self.home / "broken").mkdir()
    (self.home / "broken" / "stellaxis.db").mkdir()
    core = Core(self.home / "broken", self.home / ".claude", COMPS)
    self.assertTrue(core.error)
    srv = server.serve(core, 0, dict(CFG))
    # start it in a thread, GET /api/workspaces with http.client, shut it down in addCleanup
    with mock.patch.object(W, "live", return_value=[]):
        status, body = self.get(srv, "/api/workspaces")
    self.assertEqual(status, 200)
    self.assertEqual(json.loads(body)["workspaces"], [])

def test_write_index_reports_a_reader_error_and_a_load_error(self):
    snap = {"sessions": [ROW], "projects": []}
    with mock.patch.object(C, "_core", self.core):
        with mock.patch.object(W, "live", return_value=[]), mock.patch.object(W, "app_records", return_value={}):
            self.assertIsNone(C.write_index(dict(CFG), snap))
        with mock.patch.object(W, "live", side_effect=ValueError("boom")):
            self.assertEqual(C.write_index(dict(CFG), snap), "ValueError: boom")
        [c] = [c for c in self.core.components if c.name == "project_management"]
        with mock.patch.object(c, "error", "ImportError: gone"):
            self.assertEqual(C.write_index(dict(CFG), snap), "ImportError: gone")
    with mock.patch.object(C, "_core", Core(self.home, self.home / ".claude", self.home / "no-components")):
        self.assertEqual(C.write_index(dict(CFG), snap), "the project_management component is missing")

def test_the_uncapped_session_list_reaches_the_reader(self):
    cfg = {**CFG, "_sessions_all": [ROW, {**ROW, "id": "s2"}]}
    with mock.patch.object(C, "_core", self.core), mock.patch.object(W, "live", return_value=[]), \
            mock.patch.object(W, "app_records", return_value={}):
        self.assertIsNone(C.write_index(cfg, {"sessions": [ROW], "projects": []}))
    self.assertNotIn("_sessions_all", cfg)
    ids = [s["id"] for s in json.loads((self.home / "workspaces.json").read_text())["workspaces"][0]["repos"][0]["branches"][0]["sessions"]]
    self.assertEqual(sorted(ids), ["s1", "s2"])
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest tests.test_project_management -v`
Expected: FAIL (no `project_management` component; `W` is not `centre`).

- [ ] **Step 3: Move the file and write the alias and the component**

- `git mv collector/workspaces.py collector/components/project_management/centre.py`. Do not change its content in this task.
- New `collector/workspaces.py`:

```python
"""Old name of the command centre. The code lives in components/project_management/centre.py.
This alias keeps `import workspaces` working, as the same module object, until step 2B."""
import sys
from pathlib import Path

from core.component import import_folder

sys.modules[__name__] = import_folder(Path(__file__).resolve().parent / "components" / "project_management").centre
```

- `collector/components/project_management/__init__.py` does `from . import centre` and defines the two functions:
  - `index_reader(ctx)`: what `collect.write_index` does today, in the same order: `centre.clear_stale(centre.live(centre._bin(ctx.cfg)))`, then write `centre.build_index(ctx.inputs["sessions"], ctx.inputs["folders"], centre.app_records(centre.app_base()), ctx.cfg)` to `ctx.home / "workspaces.json"` with `core.util.write_json`. `ctx.home` is the core's home, which is `collect.STX_HOME` in the service: the same file `write_index` writes today. When `"sessions"` is not in `ctx.inputs` it returns without doing anything.
  - `handle(handler, method, ctx)`: `return centre.handle(handler, method, ctx.cfg)`.

- [ ] **Step 4: Wire `collector/collect.py`**

- `write_index(cfg, snap)` keeps its name, its docstring's promise (never fails a collection, returns the error) and the `cfg.pop("_sessions_all", None)`. It now runs `the_core().run_pass(cfg, {"sessions": <the popped list, else snap["sessions"]>, "folders": snap["projects"]})` and returns, in this order: `"the project_management component is missing"` when the core has no such component; that component's `error` when it has one; the first entry of its `errors` in the pass result; else `None`.
- `collect()`: drop the separate `the_core().run_pass(cfg)` line; `write_index` is now the one call that runs the pass.
- `Handler.do_GET` and `Handler.do_POST`: remove the direct `workspaces.handle(...)` calls. The core's `handle`, called just above, now reaches these routes. First confirm that no path is answered by both `settings_api.handle` and `centre.handle`; if one is, stop and report it.
- Keep `import workspaces` at the top of `collect.py`: tests patch `C.workspaces`.

- [ ] **Step 5: Stop one test class from building the real core**

The index is now written to the home of the cached core, so a test that patches `C.STX_HOME` must not meet a core built on another home. In every test class or test that patches `C.STX_HOME` and then runs `C.collect`, `C.write_index` or the real `C.Handler` (in `tests/test_hardening.py`: `Service`, `RouteMatrix` and the class that holds `test_index_error_stays_on_the_idle_status`; search the other test files too), set `C._core = None` right after the patch and again when the patch is undone, so each gets a core built on its own folders and leaves none behind. `Service` has no temp home today: give it one the way `RouteMatrix` does (`mock.patch.object(C, "STX_HOME", …)` on a temp folder), so running `-k Service` alone writes nothing into the real `~/.stellaxis`. Add no assertions; the existing tests of those classes must pass with no other edit.

- [ ] **Step 6: `CLAUDE.md`**

In the Architecture section, in the file's own style: `collector/workspaces.py` is now an alias; the command centre code is `collector/components/project_management/centre.py`; the component's reader writes `workspaces.json` from the pass inputs, and its routes are served through the core.

- [ ] **Step 7: Run the new tests, then the whole suite**

Run: `python3 -W error -X dev -m unittest tests.test_project_management` then `python3 -m unittest discover -s tests`
Expected: 7 tests OK with clean output, then 219 tests OK. `tests/test_workspaces.py` passes without any edit; that is the proof the move changed nothing.

- [ ] **Step 8: Commit**

```bash
git add -A collector tests CLAUDE.md
git commit -m "Project management: the command centre is the first component, served through the core"
```

---

### Task 3: Jarvis moves apart

**Files:**
- Create: `collector/unplaced/__init__.py`, `collector/unplaced/jarvis.py`, `tests/test_jarvis.py`
- Modify: `collector/components/project_management/centre.py` (the Jarvis code leaves), `collector/collect.py` (`Handler.do_GET`, `Handler.do_POST`), `tests/test_workspaces.py` (the Jarvis tests leave), `tests/test_core.py` (`IndependenceTest`), `CLAUDE.md`

**Interfaces:**
- Consumes: the alias `import workspaces as W` (Task 2).
- Produces:

```python
# collector/unplaced/jarvis.py
def handle(handler, method: str, cfg: dict) -> bool
# GET /api/jarvis; POST /api/jarvis/ask, /api/jarvis/hear, /api/jarvis/say, /api/jarvis/hush. False when the path is not one of these.
```

- [ ] **Step 1: Move the tests first, and see them fail**

Move the class `JarvisAskTest`, and every other test in `tests/test_workspaces.py` that calls a `/api/jarvis` route or a Jarvis function, into `tests/test_jarvis.py`. In the moved code change only the module the Jarvis names come from: `import unplaced.jarvis as J`, then `J.handle`, `J._ASK_LOCK`, `J.transcribe`, `J.ask`, `J.say` and so on. Keep every assertion. Names that stay in the command centre (for example a fake `claude` helper or `W.ApiError`) keep coming from `W` or are copied with the helper they belong to.

Add one test to `tests/test_jarvis.py`:

```python
def test_the_command_centre_no_longer_answers_jarvis_routes(self):
    for name in ("_jarvis", "_jarvis_ask", "_jarvis_voice", "transcribe", "ask", "say", "_ASK_LOCK", "_HEAR_LOCK", "_SAY_LOCK"):
        self.assertFalse(hasattr(W, name), name)
    self.assertNotIn("/api/jarvis", inspect.getsource(W.handle))
```

Run: `python3 -m unittest tests.test_jarvis -v`
Expected: FAIL, `No module named 'unplaced'`.

- [ ] **Step 2: Move the code**

- `collector/unplaced/__init__.py` holds only a docstring: code that runs today and is in no component yet; see "Not placed yet" in the core spec.
- Move the whole Jarvis group out of `centre.py` into `collector/unplaced/jarvis.py`, unchanged: `_jarvis`, `MAX_AUDIO`, `_ASK_LOCK`, `JARVIS_ASK`, `_tool`, `transcribe`, `_ask_context`, `ask`, `_audio`, `_HEAR_LOCK`, `_SAY_LOCK`, `_saying`, `say`, `_jarvis_voice`, `_jarvis_ask`. The file imports `settings_api` and `import workspaces as W`, and reaches the command centre names it uses through `W` (`W._home`, `W._bin`, `W._prompt`, `W._index`, `W.live`, `W.pending`, `W.ApiError`). Nothing else changes in those functions.
- `jarvis.handle` holds the three Jarvis branches that leave `centre.handle`, with the same paths, methods and handlers. Update the docstring of `centre.handle`.
- `collect.py`: in `Handler.do_GET` and `Handler.do_POST`, after the core's `handle` and before `settings_api.handle`, add `if jarvis.handle(self, <method>, load_config()): return`, with `from unplaced import jarvis` among the imports.
- `tests/test_core.py` `IndependenceTest`: add `"unplaced"` to the set of names the core must not import.

- [ ] **Step 3: `CLAUDE.md`**

In the Architecture section and in the API routes paragraph: the Jarvis routes are served by `collector/unplaced/jarvis.py`, which is in no component yet.

- [ ] **Step 4: Run the Jarvis tests, then the whole suite**

Run: `python3 -W error -X dev -m unittest tests.test_jarvis` then `python3 -m unittest discover -s tests`
Expected: all OK with clean output, then 220 tests OK (219 plus the one new test; moved tests keep their count). `RouteMatrix` in `tests/test_hardening.py` passes unedited: it calls every Jarvis route through the real `Handler`, with our Host and with a foreign one.

- [ ] **Step 5: Commit**

```bash
git add -A collector tests CLAUDE.md
git commit -m "Jarvis: its routes and voice code move out of the command centre, into unplaced/"
```

- [ ] **Step 6: Check the real service (only when the owner says so)**

```bash
launchctl kickstart -k gui/$(id -u)/com.stellaxis.collector
```

```bash
curl -s http://127.0.0.1:7777/api/core
```

Expected: one component, `project_management`, with `"readers": 1` and `"error": null`; after the first pass, `last_pass` shows it with no errors. Then `curl -s http://127.0.0.1:7777/api/workspaces` answers the same projects as before, `curl -s http://127.0.0.1:7777/api/jarvis` answers 200, and `http://127.0.0.1:7777/overview` and `http://127.0.0.1:7777/workspace?id=stellaxis` load as before.

---

## What this plan leaves for later plans

- **Step 2B, the rename:** new names `/api/projects…`, `/project?id=` and the key `projects`, with the old ones as aliases; the pages and `agents/jarvis.md` use the new names; "unassigned" replaces "other"; the config change as an action with a dry run, which needs `Ctx.act`, the unique manifest folder, undo steps owned by a component and `restore` refusing an unknown step; what the pages show when the store is down; check 5 of the spec.
- **Step 3:** records of this component in the store, the shared transcript helper, `relocate` as an action of this component, and Claude management.

## Found while building step 2A, for the step 2B plan

- **A named task: a broken `project_management` component must not stop the service.** `collect.py` imports the alias and `unplaced.jarvis` at module level, and `jarvis.py` imports the alias again, so an exception in the component's `__init__.py` or `centre.py` stops `collect.py serve`, `prune.py` and `mcp_server.py` at import. This equals the behaviour before the move, but it is against the spec's "a core that needs a component to start". Removing the alias does not fix it, because Jarvis imports the command centre at import time. Guard both imports, or have Jarvis reach the component through the core, and add a test that starts the real `Handler` with a component that raises.
- **Put the new names in the component's `handle` wrapper** (`components/project_management/__init__.py`), not in `centre.handle`, so the alias layer sits in one place.
- **`collector/unplaced/jarvis.py` reads the old names too:** `cfg["workspaces"]`, `W._index()["workspaces"]`, and a "workspace | repo | …" header in the prompt it builds.
- **Two sources for the home folder inside the component:** the reader writes to `ctx.home`, the rest of `centre.py` reads the `STELLAXIS_HOME` environment variable. Equal in the service; settle it when the index file is renamed.
- **Tests:** give the `C._core = None` reset one helper (it is in four places in `tests/test_hardening.py`). Never create a temp component folder named `project_management` in a test: it would replace the real module while `sys.modules["workspaces"]` keeps the old one.
- **Still open from step 1's list:** `Ctx.act`, the unique manifest folder, undo steps owned by a component, `restore` refusing an unknown step, `run_action` with the store down, the "POSTs need `application/json`" rule in the core, a schema-change rule.
- **For step 3:** `index_error` in `status.json` reports only this component's reader errors. `README.md` still names `collector/workspaces.py` as the command centre. The rule "a component imports no `workspaces`, `collect` or `unplaced`" stops being enough if `settings_api` becomes an alias of a component the way `workspaces` did; then widen the rule, or import components through Python's own import system.
- **For the owner to rule on:** the Jarvis code still saves every recording to `~/.stellaxis/jarvis/heard/` with no limit. It is marked "TEMP" in the code and moved unchanged.
