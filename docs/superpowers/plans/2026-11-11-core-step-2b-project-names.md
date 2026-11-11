# Stellaxis Core, Step 2B (The Word Becomes "Project", and the Core Contract Gaps Close) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the word "workspace" with "project" in routes, pages, files, config and code, keep every old name working as an alias, and close the gaps in the core contract that steps 1 and 2A listed.

**Architecture:** The command centre code (`centre.py`) uses only the new names. One file in the component, `old_names.py`, is the alias layer: it turns old config keys, old routes, the old body key and the old id into the new ones on the way in, and gives old routes and the old index file their old shape on the way out. The component's `handle` wrapper is the only caller of it. Jarvis and `collect.py` stop importing the component; Jarvis reads what it needs through the core, so a broken component cannot stop the service. The config file changes only through an action of the component, with a dry run, a backup and an undo step.

**Tech Stack:** Python 3 stdlib, `unittest`.

**Spec:** `docs/superpowers/specs/2026-11-11-core-architecture-design.md` (words follow `CONTEXT.md`): sections "2. Project management", "Names during the move", "Decisions made by the writer" (the config bullet), "How success is checked" (check 5). Also the sections "Found while building …" at the end of `2026-11-11-core-step-1-empty-core.md` and `2026-11-11-core-step-2a-first-component.md`.

## Global Constraints

- Python 3 standard library only. No packages, no build step.
- macOS, the launchd service `com.stellaxis.collector`, port 7777, 127.0.0.1 only.
- All tests pass after every task (224 before Task 1): `python3 -m unittest discover -s tests`. Output is clean: no warnings, no stray prints.
- Every existing page, API route, the desktop MCP tool and the artifact path of `index.html` and `hierarchy.html` keep working after every task. Old names answer as they did before.
- Every change the product makes to files or settings is a dry run until applied.
- `prune.py` keeps its exact command line and output.
- The core (`collector/core/`) imports nothing outside itself. Every file in it starts with `from __future__ import annotations`.
- Nothing under `collector/components/` imports `collect` or `unplaced`.
- Never use `git stash`. Never run `launchctl` or `collect.py serve`. Never touch port 7777. Never change the real `~/.stellaxis` or `~/.claude`: tests use a fake home (`STELLAXIS_HOME`, `HOME`, `CLAUDE_CONFIG_DIR`). Work only in the worktree. Do not push.
- Never create a temp component folder named `project_management` in a test: it would replace the real module for every later test.
- In tests, get the command centre module with `core.component.import_folder(REPO / "collector" / "components" / "project_management").centre`, never by another import.
- Read `DESIGN.md` before changing any page.
- End each commit message with a blank line and `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.
- The old word may be written only in `collector/components/project_management/old_names.py`, `tests/test_old_names.py`, `docs/`, `CONTEXT.md` and `README.md`. This holds for `collector/` and `tests/` from the end of Task 3, for `dashboard/`, `agents/` and `DESIGN.md` from the end of Task 4, and for `CLAUDE.md` from the end of Task 6. Anywhere else, a test that must name it builds it from two parts.

## Decisions made by the writer (my calls), with the cost if wrong

1. **The Python alias module `collector/workspaces.py` is deleted.** The 2A plan kept it "until step 2B". Cost: a script outside this repo that imports it breaks; none is known.
2. **Jarvis reaches Project management through the core** (`Core.read`, new, for ways in), not by import. With a broken component Jarvis still answers; it only knows no sessions. Cost: when the ways in get their own design, `Core.read` may be replaced; it is 8 lines.
3. **The alias layer is one file inside the component, `old_names.py`,** and its tests are one file, `tests/test_old_names.py`. Both count as "the alias layer" of the spec. Cost: none.
4. **The old page addresses redirect (302)** to `/project?id=…` and `/project.html`. Cost: a tool that does not follow redirects sees 302, not the page.
5. **Old routes answer in the old shape exactly:** the key `workspaces`, and the unassigned group shown with id `other` and name "Other". `workspaces.json` is written beside `projects.json` in that same old shape. Cost: none known.
6. **The old id `other` is understood only under old names** (old routes, old page address, old body key). `/api/projects/other/tasks` is "unknown project", unless a project has that id. Cost: a caller that mixes a new route with the old id gets 404.
7. **The notes cards move from the config key `projects` to `project_notes`.** Cost: one key to rename again.
8. **How the service tells an old config from a new one:** it has the key `workspaces`. For the notes: `project_notes` when it has entries, else every entry of `projects` that has no `id` (a card never had one). Cost: a notes card that carries an `id` would be hidden until the rename is applied.
9. **The unassigned task list is `tasks/unassigned.md`.** While that file is absent and `tasks/other.md` exists, the old file is read and written. The rename action moves it. Cost: none; nothing is lost either way.
10. **The rename is reached over HTTP:** `GET /api/projects/new-names` is the dry run and `POST /api/projects/new-names` with `{"apply": true}` applies it, through `Ctx.act`. No command line. Cost: it needs the service running; a command line would be about 15 lines more.
11. **Undo of the config rename puts the old keys back** in the file as it is then (edits made after the rename are kept). The untouched backup stays in the manifest folder. Cost: the restored file is formatted with indent 2, not byte-equal to the backup.
12. **An undo step owned by a component is named `<component name>.<step>`.** The loader refuses any other name. Cost: none.
13. **A manifest folder name is unique inside one process and against folders already on disk** (`-2`, `-3` … added). Two different processes in the same second with the same label can still collide, as before. Cost: `prune.py` run twice in one second, which no one does.
14. **`run_action` runs when the store is down.** This follows the 2A ruling and replaces step 1's "refuse it". Only an action that calls `ctx.db()` fails, and that failure is logged. Cost: none; the rename action needs no store.
15. **The "POSTs need `application/json`" rule stays in the body reader** (`settings_api.read_body`), not in the core, because Jarvis takes audio. Cost: a new component can forget the check; the components README says to use the reader.
16. **Inside the component the home folder has one source, `centre._home()`** (the `STELLAXIS_HOME` variable). The reader no longer writes to `ctx.home`. Cost: a `Core` built on another home than the variable would write the index to the variable's home; the test helper sets both.
17. **When the store is down, pages work as before and the status line in the sidebar names the store error.** Cost: one line of page text.
18. **`README.md` is left for step 3** and is allowed in the test of check 5 until then.

## Before starting

- Branch `core-architecture` is local, not pushed, and belongs to the main session. Work on the branch of this worktree only. The main session merges and restarts the service.
- Another session builds `collector/components/memory_management/` in parallel. Do not touch that folder.

## Review Focus

1. A config in the old shape, or one with only notes cards under `projects`: projects and cards both still show. Tests in Task 3.
2. A client that still uses old names after the restart (an open browser tab, the installed copy of the Jarvis agent): old routes, the id `other`, the body key, the old page address and `workspaces.json` answer as before. Tests in Task 3.
3. Two applied actions in the same second: two manifest folders. Test in Task 1.
4. An archive with a step whose owner is not loaded: `restore` refuses and changes nothing. Test in Task 1.
5. The rename applied twice, or undone after `config.json` was edited: the second apply changes nothing; the undo keeps the edit. Tests in Task 5.
6. The component raises at import: `collect.py`, `prune.py` and `mcp_server.py` still import, and the real `Handler` still serves. Test in Task 2.

## File Structure

| File | Responsibility |
|---|---|
| `collector/core/app.py` | Adds `Ctx.act`, `Core.read`, `Core.undo_handlers`. |
| `collector/core/component.py` | Adds `Component.undo` and its name rule. |
| `collector/core/undo.py` | Unique manifest folder; `restore` refuses an unknown step. |
| `collector/prune.py` | `restore` also knows the undo steps of components. |
| `collector/components/project_management/centre.py` | The command centre, new names only. |
| `collector/components/project_management/old_names.py` | The alias layer. The only code that may say the old word. |
| `collector/components/project_management/__init__.py` | `COMPONENT`: reader, routes, offers, the rename action and its undo step. |
| `collector/workspaces.py` | Deleted. |
| `collector/unplaced/jarvis.py` | Reads Project management through the core. |
| `collector/collect.py` | No import of the component; new config keys; page `/project`. |
| `dashboard/project.html` | Was `workspace.html`. |
| `dashboard/home.html`, `dashboard/shell.html`, `agents/jarvis.md`, `DESIGN.md` | New names and text. |
| `tests/test_projects.py` | Was `tests/test_workspaces.py`. |
| `tests/test_old_names.py` | Tests of the alias layer and of the rename action. |
| `tests/test_core.py`, `tests/test_project_management.py`, `tests/test_hardening.py`, `tests/test_jarvis.py`, `tests/test_e2e.py` | Updated. |

---

### Task 1: The core contract gaps

**Files:**
- Modify: `collector/core/app.py`, `collector/core/component.py`, `collector/core/undo.py`, `collector/prune.py` (`restore`), `collector/components/README.md`
- Test: `tests/test_core.py`

**Interfaces:**
- Produces:

```python
# core/component.py
@dataclass
class Component:
    ...
    undo: dict = field(default_factory=dict)   # step name -> handler(entry: dict, apply: bool, out) -> bool
# _import refuses (ValueError, so the component loads with an error) any key of `undo` that does not start with "<name>."

# core/app.py
class Ctx:
    def act(self, action: str, args: dict | None = None, apply: bool = False) -> dict
    # Core.run_action for this component's own name and config. Same return value.
class Core:
    def read(self, owner: str, offer: str, cfg: dict, **args) -> list
    # For ways in: what `owner` offers. [] when the owner is missing, has an error, or has no such offer.
    def undo_handlers(self) -> dict
    # Every undo step of every component that loaded without error.

# core/undo.py
def restore(name, apply, archive, handlers=None, on_applied=None, out=print) -> None
# Before any step runs: an entry whose op is in no table and is not "json_backup" raises
# RestoreError(f"no undo for step {op!r}; nothing was changed").
```

`Ctx.read` keeps its rule (a `LookupError` for an undeclared read) and then calls `Core.read`.

- [ ] **Step 1: Write the failing tests** (`tests/test_core.py`)

In `LoadTest`:

```python
def test_an_undo_step_must_carry_the_component_name(self):
    put(self.comps, "alpha", "from core.component import Component\nCOMPONENT = Component(name='alpha', undo={'alpha.back': print})\n")
    put(self.comps, "beta", "from core.component import Component\nCOMPONENT = Component(name='beta', undo={'back': print})\n")
    a, b = component.load(self.comps)
    self.assertEqual((a.error, list(a.undo)), (None, ["alpha.back"]))
    self.assertIn("ValueError", b.error)
    self.assertIn("beta.", b.error)
```

In `UndoTest`: remove the `future_op` entry from `old_archive()` (it was never a real step). `test_restore_stops_when_a_handler_says_no` adds that entry to the manifest itself before it calls `restore`. Add:

```python
def test_two_applied_plans_in_one_second_get_two_folders(self):
    with mock.patch.object(undo, "stamp", return_value="20261111-120000"):
        plans = [undo.Plan(True, "t", self.arch, out=lambda _: None) for _ in range(3)]
        dry = undo.Plan(False, "t", self.arch, out=lambda _: None)
    self.assertEqual([p.dir.name for p in plans], ["20261111-120000-t", "20261111-120000-t-2", "20261111-120000-t-3"])
    self.assertEqual(dry.dir.name, "20261111-120000-t")
    self.assertFalse(self.arch.exists())          # naming a folder creates nothing

def test_a_folder_already_on_disk_is_not_reused(self):
    (self.arch / "20261111-120001-t").mkdir(parents=True)
    with mock.patch.object(undo, "stamp", return_value="20261111-120001"):
        self.assertEqual(undo.Plan(True, "t", self.arch, out=lambda _: None).dir.name, "20261111-120001-t-2")

def test_restore_refuses_a_step_it_has_no_handler_for(self):
    d, cfg = self.old_archive()
    before = cfg.read_text()
    man = json.loads((d / "manifest.json").read_text())
    man["entries"].insert(0, {"op": "ghost.step"})      # restore runs entries last to first, so this one would run last
    (d / "manifest.json").write_text(json.dumps(man))
    for apply in (False, True):
        with self.assertRaisesRegex(undo.RestoreError, r"no undo for step 'ghost.step'; nothing was changed"):
            undo.restore(d.name, apply, self.arch, out=lambda _: None)
    self.assertEqual(cfg.read_text(), before)
    self.assertTrue((d / "manifest.json").exists())
    undo.restore(d.name, True, self.arch, handlers={"ghost.step": lambda e, apply, out: True}, out=lambda _: None)
    self.assertTrue((d / "manifest.restored.json").exists())
```

In `ActionTest`, add to the `ACT` source a third action and an undo step, and declare them (`actions={"touch": touch, "refuse": refuse, "note": note}, undo={"alpha.unnote": unnote}`):

```python
def note(plan, ctx, path):
    plan.say(f"note {path}")
    plan.manifest["entries"].append({"op": "alpha.unnote", "path": path})
    if plan.apply:
        Path(path).write_text("n")
    return True
def unnote(e, apply, out):
    out("remove " + e["path"])
    if apply:
        Path(e["path"]).unlink()
    return True
def uses_db(plan, ctx):
    ctx.db()
    return True
```

(`uses_db` is declared as the action `"uses_db"` too.) Then:

```python
def test_a_component_runs_its_own_action_through_the_core(self):
    ctx = self.core.ctx("alpha", {})
    self.assertEqual(ctx.act("touch", {"path": self.f}), {"applied": False, "ok": True, "steps": [f"create {self.f}"], "manifest": None})
    self.assertFalse(Path(self.f).exists())
    r = ctx.act("touch", {"path": self.f, "keep": True}, apply=True)
    self.assertTrue(Path(self.f).exists())
    [row] = [json.loads(l) for l in self.log.read_text().splitlines()]
    self.assertEqual((row["component"], row["action"], row["manifest"]), ("alpha", "touch", r["manifest"]))

def test_two_applied_actions_in_one_second_keep_two_manifests(self):
    with mock.patch.object(undo, "stamp", return_value="20261111-120002"):
        a = self.core.run_action("alpha", "touch", {"path": self.f, "keep": True}, True, {})
        b = self.core.run_action("alpha", "touch", {"path": self.f + "2", "keep": True}, True, {})
    self.assertNotEqual(a["manifest"], b["manifest"])
    self.assertTrue(Path(a["manifest"]).is_file() and Path(b["manifest"]).is_file())

def test_a_component_undo_step_restores_through_the_core_table(self):
    r = self.core.run_action("alpha", "note", {"path": self.f}, True, {})
    arch, name = self.home / ".claude" / "_archive", Path(r["manifest"]).parent.name
    with self.assertRaisesRegex(undo.RestoreError, "no undo for step 'alpha.unnote'"):
        undo.restore(name, True, arch, out=lambda _: None)
    self.assertEqual(sorted(self.core.undo_handlers()), ["alpha.unnote"])
    undo.restore(name, True, arch, handlers=self.core.undo_handlers(), out=lambda _: None)
    self.assertFalse(Path(self.f).exists())

def test_actions_run_with_the_store_down_and_only_a_store_user_fails(self):
    home = self.home / "down"
    (home / "stellaxis.db").mkdir(parents=True)
    core = Core(home, home / ".claude", self.comps)
    self.assertTrue(core.error)
    self.assertTrue(core.run_action("alpha", "touch", {"path": self.f}, True, {})["ok"])
    with self.assertRaises(StoreError):
        core.run_action("alpha", "uses_db", {}, True, {})
    rows = [json.loads(l) for l in (home / "actions.jsonl").read_text().splitlines()]
    self.assertEqual([(r["action"], r["ok"]) for r in rows], [("touch", True), ("uses_db", False)])
    self.assertTrue(rows[1]["error"].startswith("StoreError: "))
```

In `CoreTest` (same set-up as `test_reads_go_through_declared_offers_only`):

```python
def test_a_way_in_reads_an_offer_without_declaring_it(self):
    put(self.comps, "alpha", ALPHA); put(self.comps, "beta", BETA)
    put(self.comps, "broken", "raise RuntimeError('boom')\n")
    c = self.core(); c.run_pass({"x": 7})
    self.assertEqual(c.read("alpha", "things", {}), [{"id": "7"}])
    self.assertEqual(c.read("alpha", "things", {}, prefix="z"), [])
    self.assertEqual(c.read("alpha", "no_such_offer", {}), [])
    self.assertEqual(c.read("ghost", "things", {}), [])
    self.assertEqual(c.read("broken", "things", {}), [])
```

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest tests.test_core -v 2>&1 | tail -30`
Expected: the new tests FAIL (`Ctx` has no `act`, `Core` has no `read`, folder names equal, `restore` does not raise, `Component` has no `undo`).

- [ ] **Step 3: Implement**

- `core/undo.py`: `Plan.__init__` gets its folder from a new function `_free(archive: Path, label: str, reserve: bool) -> Path`. With `reserve` true it holds a module lock, starts from `f"{stamp()}-{label}"`, adds `-2`, `-3` … while the full path is in a module-level set of paths handed out or exists on disk, records the path in the set and returns it. With `reserve` false (a dry run) it returns the plain name. It creates nothing. `restore` checks every entry's `op` against the merged table before the loop.
- `core/component.py`: the `undo` field; in `_import`, after the name checks, the rule for the keys of `comp.undo`.
- `core/app.py`: `Ctx.act`, `Core.read`, `Core.undo_handlers`; `Ctx.read` delegates to `Core.read` after its own check.
- `collector/prune.py`, `restore`: `handlers={"relocate": undo_relocate, **C.the_core().undo_handlers()}`. Nothing else in the file changes.
- `collector/components/README.md`: add four lines. A component runs its own action with `ctx.act(action, args, apply)`, so it gets the dry run, the manifest and the log line. An action that must be undoable adds a manifest entry whose `op` is `<component name>.<step>` and declares the handler in `Component(undo={...})`; `restore` refuses a manifest with a step that has no handler. Actions run also when the store is down; only `ctx.db()` fails. A route reads a JSON body with `settings_api.read_body`, which refuses other content types.

- [ ] **Step 4: Run the file, then the whole suite**

Run: `python3 -W error -X dev -m unittest tests.test_core` then `python3 -m unittest discover -s tests`
Expected: OK with clean output, then all tests OK (224 plus the new ones). `tests/test_e2e.py` and `tests/test_relocate.py` pass unedited: that is the proof `prune.py` did not change for its users.

- [ ] **Step 5: Commit**

```bash
git add collector/core collector/prune.py collector/components/README.md tests/test_core.py
git commit -m "Core: a component runs its own action, owns its undo steps, and gets a manifest folder of its own"
```

---

### Task 2: A broken component cannot stop the service

**Files:**
- Delete: `collector/workspaces.py`
- Modify: `collector/components/project_management/__init__.py` (offers), `collector/unplaced/jarvis.py`, `collector/collect.py` (imports, the two `jarvis.handle` calls, `serve`), `tests/test_jarvis.py`, `tests/test_workspaces.py`, `tests/test_hardening.py`, `tests/test_project_management.py`

No name changes in this task: routes, keys, files and page text stay as they are. Only where the code is imported from changes.

**Interfaces:**
- Consumes: `Core.read` (Task 1).
- Produces:

```python
# components/project_management/__init__.py
COMPONENT = Component(name="project_management", readers=[index_reader], handle=handle, offers={
    "index": ...,      # (ctx) -> the list of groups in the index file, each with repos > branches > sessions; [] when there is no index
    "projects": ...,   # (ctx) -> the configured entries that have an id (centre._entries of the config list)
    "live": ...,       # (ctx) -> rows of `claude agents --json`; [] when it cannot tell
    "approvals": ...,  # (ctx) -> centre.pending()
})

# collector/unplaced/jarvis.py
def handle(handler, method: str, cfg: dict, core) -> bool
def transcribe(audio: bytes, cfg: dict, core) -> str
def ask(question: str, cfg: dict, core) -> str
```

- [ ] **Step 1: Write the failing tests**

`tests/test_project_management.py`, new class. It copies `collector/` and `dashboard/` to a temp folder (ignore `__pycache__`), replaces the copy's `components/project_management/__init__.py` with `raise RuntimeError("boom")`, and runs one Python child process in the copy with `HOME`, `STELLAXIS_HOME` and `CLAUDE_CONFIG_DIR` pointing into the temp folder:

```python
class BrokenComponentTest(unittest.TestCase):
    """The spec's anti-goal: a core that needs a component to start."""
    CHILD = '''
import http.client, http.server, json, sys, threading
import collect, prune, mcp_server          # each must import with the component broken
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), collect.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
def get(path):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    c.request("GET", path); r = c.getresponse(); return r.status, r.read().decode()
out = {"core": json.loads(get("/api/core")[1]), "overview": get("/overview")[0], "jarvis": get("/api/jarvis")[0],
       "index_error": collect.write_index({}, {"sessions": [], "projects": []})}
srv.shutdown(); print(json.dumps(out))
'''
    def test_the_service_starts_and_serves_with_the_component_broken(self):
        out = self.child()      # runs CHILD with cwd = the copied collector folder, returns the parsed last line
        [c] = [c for c in out["core"]["components"] if c["name"] == "project_management"]
        self.assertEqual(c["error"], "RuntimeError: boom")
        self.assertEqual((out["overview"], out["jarvis"]), (200, 200))
        self.assertEqual(out["index_error"], "RuntimeError: boom")
```

The child must end with exit code 0 and an empty stderr; assert both.

`tests/test_project_management.py`, in `ProjectManagementTest`: replace `test_the_old_name_is_the_same_module` with

```python
def test_nothing_outside_the_core_imports_the_component(self):
    self.assertFalse((REPO / "collector" / ("work" + "spaces.py")).exists())
    for name in ("collect.py", "unplaced/jarvis.py", "prune.py", "mcp_server.py"):
        src = (REPO / "collector" / name).read_text()
        self.assertNotIn("import " + "work" + "spaces", src, name)
        self.assertNotIn("project_management import", src, name)

def test_ways_in_read_the_offers(self):
    (self.home / "workspaces.json").write_text(json.dumps({"workspaces": [{"id": "x", "name": "X", "repos": []}]}))
    with mock.patch.object(W, "live", return_value=None), mock.patch.object(W, "pending", return_value=[{"id": "r"}]):
        self.assertEqual([g["id"] for g in self.core.read("project_management", "index", CFG)], ["x"])
        self.assertEqual(self.core.read("project_management", "live", CFG), [])
        self.assertEqual(self.core.read("project_management", "approvals", CFG), [{"id": "r"}])
    cfg = {**CFG, "workspaces": [{"id": "p", "name": "P"}, {"name": "no id"}]}
    self.assertEqual([p["id"] for p in self.core.read("project_management", "projects", cfg)], ["p"])
    self.assertEqual(self.core.read("project_management", "index", CFG), self.core.read("project_management", "index", CFG))
    (self.home / "workspaces.json").unlink()
    self.assertEqual(self.core.read("project_management", "index", CFG), [])
```

The index file, its key and the config key carry today's names in this task; Task 3 renames them in this test with all the others. In `IndependenceTest` the banned set becomes `{"collect", "unplaced"}`.

`tests/test_jarvis.py`: every `J.handle(self, m, cfg)` becomes `J.handle(self, m, cfg, core)` with `core = Core(<the test's STELLAXIS_HOME folder>, <its claude folder>, COMPS)`. The assertion `"Jarvis, Stellaxis, PartHub"` stays and must still pass (the name now arrives through the `projects` offer).

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest tests.test_project_management tests.test_jarvis 2>&1 | tail -20`
Expected: FAIL (the child exits with `RuntimeError: boom` at `import collect`; the alias file exists; `handle()` takes 3 arguments).

- [ ] **Step 3: Implement**

- `components/project_management/__init__.py`: the four offers.
- `collector/unplaced/jarvis.py`: remove `import workspaces as W`. `ApiError` is `settings_api.ApiError`. The claude binary is `_tool(cfg, "claude_bin", "~/.local/bin/claude")`. The home folder is `core.home`. `_prompt` and `MAX_PROMPT` are copied into this file unchanged (the route keeps its limits and messages). Sessions, live rows, approvals and the names for the Whisper prompt come from `core.read("project_management", <offer>, cfg)`. `core` is passed down from `handle` to every function that needs it.
- `collector/collect.py`: remove `import workspaces` and its comment. Both `jarvis.handle(...)` calls pass `the_core()` as the fourth argument. `serve()` calls `the_core()` once before it starts any thread, so the component is imported on the main thread as before.
- Delete `collector/workspaces.py`.
- Tests: in `tests/test_workspaces.py`, `tests/test_jarvis.py`, `tests/test_project_management.py` the module `W` now comes from `import_folder(...).centre` (see Global Constraints). `mock.patch("workspaces.subprocess.run", …)` becomes `mock.patch.object(W.subprocess, "run", …)`. In `tests/test_hardening.py`, `C.workspaces` becomes that same module, and the four places that reset `C._core` use one new helper:

```python
def fake_home(case, stx: Path, claude: Path | None = None) -> None:
    """Point collect, the command centre and a fresh core at a temp home, until the test (or class) ends."""
```

`claude` defaults to `stx / ".claude"`. It patches the environment (`STELLAXIS_HOME`, `CLAUDE_CONFIG_DIR`), `C.STX_HOME` and `C.CLAUDE_DIR` (the core that `the_core()` builds writes its manifests under `C.CLAUDE_DIR`, so an unpatched one would be the real `~/.claude`), sets `C._core = None`, and registers the undo of all of it with `case.addCleanup` (or `addClassCleanup` when `case` is a class). `Service`, `RouteMatrix` and both tests of `IndexFailure` use it. Their assertions do not change.

- [ ] **Step 4: Run the changed files, then the whole suite**

Run: `python3 -W error -X dev -m unittest tests.test_project_management tests.test_jarvis tests.test_hardening` then `python3 -m unittest discover -s tests`
Expected: OK with clean output, then all tests OK.

- [ ] **Step 5: Commit**

```bash
git add -A collector tests
git commit -m "Project management: Jarvis reads it through the core, so a broken component cannot stop the service"
```

---

### Task 3: New names in the component, old names in one alias layer

**Files:**
- Create: `collector/components/project_management/old_names.py`, `tests/test_old_names.py`
- Move: `tests/test_workspaces.py` to `tests/test_projects.py`, and `dashboard/workspace.html` to `dashboard/project.html` (`git mv` both; the page's content is not changed in this task)
- Modify: `collector/components/project_management/centre.py`, `collector/components/project_management/__init__.py`, `collector/collect.py` (`DEFAULT_CONFIG`, `project_notes`, `PAGES`, the status phase, two comments), `collector/unplaced/jarvis.py` (the header line of the sessions table says "project"), `tests/test_projects.py`, `tests/test_project_management.py`, `tests/test_hardening.py`, `tests/test_jarvis.py`, `tests/test_e2e.py`

The content of the pages is not touched in this task. They keep calling the old routes and linking to the old page address, which now go through the alias layer; that is part of the proof.

**Interfaces:**
- Produces:

```python
# centre.py: no old word anywhere in the file
UNASSIGNED = "unassigned"
INDEX_FILE = "projects.json"
def assign(path: str, projects: list[dict]) -> str                 # a project id, else UNASSIGNED
def build_index(sessions, folders, app, cfg, now=None) -> dict     # {"generated_at", "projects": [..., {"id": "unassigned", "name": "Unassigned", ...}]}
def listing(cfg: dict) -> dict                                     # the index plus live status: what GET /api/projects answers
def handle(handler, method: str, cfg: dict, path: str | None = None, fix=None) -> bool
# path: the request path to route on (default: the handler's own, without the query).
# fix: applied to the JSON body of POST /api/sessions/new before it is used (default: nothing).
# Config read by centre: cfg["projects"] = [{"id", "name", "repos": [path], "tests": {repo: argv}, "routines": [name]}]
# Routes: GET /api/projects; GET, POST /api/projects/<id>/tasks; GET /api/projects/<id>/work;
#         POST /api/projects/<id>/tests/run; POST /api/sessions/new {"project", "folder", "prompt"}; the session and approval routes as today.
# Task list file: tasks/<id>.md; for UNASSIGNED, tasks/other.md while tasks/unassigned.md is absent and tasks/other.md exists.
# Messages: "unknown project", "that folder is not one of this project's repos".

# old_names.py: the alias layer
ROUTE = "/api/workspaces"
INDEX_FILE = "workspaces.json"
def config(cfg: dict) -> dict
# The config with the new keys. With the key "workspaces" present: "projects" becomes that list, each entry's
# "folders" given as "repos". Otherwise `cfg` is returned as it is. Never changes its argument.
def path(path: str) -> str | None
# "/api/workspaces/<id>/<rest>" -> "/api/projects/<id>/<rest>", the id "other" -> "unassigned". None for any other path.
def page(path: str, query: str) -> str | None
# ("/workspace", "id=other&x=1") -> "/project?id=unassigned&x=1"; ("/workspace.html", "") -> "/project.html". None for any other path.
def body(b: dict) -> dict
# {"workspace": "other", ...} -> {"project": "unassigned", ...}. A body with no old key is returned as it is.
def answer(index: dict) -> dict
# {"projects": [...], ...} -> {"workspaces": [...], ...}, the unassigned group shown with id "other" and name "Other". Other keys kept.

# components/project_management/__init__.py
def index_reader(ctx) -> None
# Writes centre._home() / "projects.json", and beside it old_names.INDEX_FILE holding old_names.answer(index).
def handle(handler, method, ctx) -> bool
# cfg = old_names.config(ctx.cfg). GET of an old page address: 302 to old_names.page(...). GET old_names.ROUTE:
# 200 with old_names.answer(centre.listing(cfg)). Everything else:
# centre.handle(handler, method, cfg, path=old_names.path(path), fix=old_names.body).
# Every offer also uses old_names.config(ctx.cfg).

# collect.py
DEFAULT_CONFIG["projects"] = []        # [{id, name, repos: [path], tests, routines}]
DEFAULT_CONFIG["project_notes"] = []   # the notes cards. No "workspaces" key in the defaults.
PAGES["/project"] = "project.html"     # replaces the old entry; the alias layer redirects the old addresses
def project_notes(cfg) -> list         # cards: cfg["project_notes"] when it has entries, else the entries of cfg["projects"] with no "id"
```

The status phase text becomes `"indexing projects"`.

- [ ] **Step 1: Rename the tests to the new names, and see them fail**

`git mv tests/test_workspaces.py tests/test_projects.py`. In it, and in `tests/test_project_management.py`, `tests/test_hardening.py`, `tests/test_jarvis.py`, `tests/test_e2e.py`, replace every old name with the new one and change nothing else in what is asserted:

| Old | New |
|---|---|
| config key `workspaces`, entry key `folders` | `projects`, `repos` |
| index key `workspaces`, file `workspaces.json` | `projects`, `projects.json` |
| routes `/api/workspaces…` | `/api/projects…` |
| body key `workspace` | `project` |
| group id `other`, name `Other` | `unassigned`, `Unassigned` |
| messages with "workspace" | the same with "project" |
| test names and comments with the old word | the new word |

Tests that call `W._list` call `W.listing` (it returns the dict; the test no longer needs a handler for it). In `tests/test_hardening.py` `RouteMatrix`: the config and index are written with the new names by two small methods of the class, `config(self) -> dict` and `routes(self)`, so a subclass can replace them; the two page rows become `("/project?id=w", 200)` and `("/project.html", 200)`. The row for the unassigned task list is `/api/projects/unassigned/tasks`. In `Service.test_pages_are_routed` the three requests for the old page address become `/project?id=x` (status 200, `id="new-session"` and `href="/overview"` in the body).

Run: `python3 -m unittest discover -s tests 2>&1 | tail -5`
Expected: many FAIL (the component still has the old names).

- [ ] **Step 2: Write the tests of the alias layer** (`tests/test_old_names.py`)

The file starts like `tests/test_project_management.py`, plus `sys.path.insert(0, str(REPO / "tests"))` and `import test_hardening`. `PM = import_folder(COMPS / "project_management")`, `W = PM.centre`, `O = PM.old_names`. `OLD_CFG = {"claude_bin": "/no/claude", "workspaces": [{"id": "x", "name": "X", "folders": ["~/code/x"], "routines": ["r"]}], "projects": [{"name": "A card"}]}`.

```python
class NamesTest(unittest.TestCase):
    def test_old_config_keys_are_read_as_the_new_ones(self):
        cfg = O.config(OLD_CFG)
        self.assertEqual(cfg["projects"], [{"id": "x", "name": "X", "repos": ["~/code/x"], "routines": ["r"]}])
        self.assertEqual(OLD_CFG["workspaces"][0]["folders"], ["~/code/x"])          # the argument is not changed
        new = {"projects": [{"id": "y", "repos": ["~/code/y"]}], "project_notes": []}
        self.assertIs(O.config(new), new)

    def test_old_paths_bodies_and_pages(self):
        self.assertEqual(O.path("/api/workspaces"), "/api/projects")
        self.assertEqual(O.path("/api/workspaces/other/tasks"), "/api/projects/unassigned/tasks")
        self.assertEqual(O.path("/api/workspaces/x/tests/run"), "/api/projects/x/tests/run")
        self.assertIsNone(O.path("/api/projects/x/tasks"))
        self.assertIsNone(O.path("/api/workspacesx"))
        self.assertEqual(O.body({"workspace": "other", "folder": "f"}), {"project": "unassigned", "folder": "f"})
        self.assertEqual(O.body({"project": "x"}), {"project": "x"})
        self.assertEqual(O.page("/workspace", "id=other&x=1"), "/project?id=unassigned&x=1")
        self.assertEqual(O.page("/workspace", "id=a%20b"), "/project?id=a%20b")
        self.assertEqual(O.page("/workspace", ""), "/project")
        self.assertEqual(O.page("/workspace.html", ""), "/project.html")
        self.assertIsNone(O.page("/project", "id=x"))

    def test_old_answers_keep_their_shape(self):
        new = {"generated_at": "g", "live_ok": True, "projects": [{"id": "x", "name": "X", "repos": []}, {"id": "unassigned", "name": "Unassigned", "repos": []}]}
        self.assertEqual(O.answer(new), {"generated_at": "g", "live_ok": True, "workspaces": [
            {"id": "x", "name": "X", "repos": []}, {"id": "other", "name": "Other", "repos": []}]})
        self.assertEqual(new["projects"][1]["id"], "unassigned")
```

`class ComponentTest` builds a temp home like `ProjectManagementTest` (environment patched, `Core(self.home, self.home / ".claude", COMPS)`), serves it with `server.serve(core, 0, cfg)` and has `get(path)` and `post(path, body)` helpers that return `(status, parsed JSON or None, headers)`:

```python
def test_a_pass_with_an_old_config_writes_both_index_files(self):
    row = {"id": "s1", "project": "~/code/x", "first_ts": "2026-11-01T00:00:00Z", "last_ts": "2026-11-01T00:00:00Z", "cost": 1, "branch": "main"}
    stray = {**row, "id": "s2", "project": "~/nowhere/y"}
    with mock.patch.object(W, "live", return_value=[]), mock.patch.object(W, "app_records", return_value={}):
        res = self.core.run_pass(dict(OLD_CFG), {"sessions": [row, stray], "folders": []})
    self.assertEqual(res["components"]["project_management"]["errors"], [])
    new = json.loads((self.home / "projects.json").read_text())
    old = json.loads((self.home / "workspaces.json").read_text())
    self.assertEqual([p["id"] for p in new["projects"]], ["x", "unassigned"])
    self.assertEqual([(w["id"], w["name"]) for w in old["workspaces"]], [("x", "X"), ("other", "Other")])
    self.assertEqual(old["workspaces"][0]["repos"], new["projects"][0]["repos"])

def test_old_routes_answer_as_before(self):
    # after the pass of the test above (put the pass in a helper), with the server running on OLD_CFG:
    status, body, _ = self.get("/api/workspaces")
    self.assertEqual((status, [w["id"] for w in body["workspaces"]], "projects" in body), (200, ["x", "other"], False))
    self.assertEqual(self.get("/api/projects")[1]["projects"][1]["id"], "unassigned")
    self.assertEqual(self.post("/api/workspaces/other/tasks", {"add": "one"})[0], 200)
    self.assertEqual([t["text"] for t in self.get("/api/projects/unassigned/tasks")[1]], ["one"])
    self.assertEqual([t["text"] for t in self.get("/api/workspaces/other/tasks")[1]], ["one"])
    self.assertEqual(self.get("/api/workspaces/x/work")[0], 200)
    self.assertEqual(self.get("/api/workspaces/nope/tasks")[0], 404)
    self.assertEqual(self.get("/api/projects/other/tasks")[0], 404)          # the old id is an old name only

def test_the_old_page_address_redirects(self):
    status, _, headers = self.get("/workspace?id=other")
    self.assertEqual((status, headers["Location"]), (302, "/project?id=unassigned"))

def test_the_old_task_file_is_used_until_it_is_moved(self):
    (self.home / "tasks").mkdir()
    (self.home / "tasks" / "other.md").write_text("- [ ] kept\n")
    self.assertEqual([t["text"] for t in W.read_tasks("unassigned")], ["kept"])
    W.write_task("unassigned", {"add": "more"})
    self.assertEqual((self.home / "tasks" / "other.md").read_text(), "- [ ] kept\n- [ ] more\n")
    self.assertFalse((self.home / "tasks" / "unassigned.md").exists())
    (self.home / "tasks" / "other.md").rename(self.home / "tasks" / "unassigned.md")
    self.assertEqual(len(W.read_tasks("unassigned")), 2)

def test_a_new_session_takes_the_old_body_key(self):
    # index with project "x" whose repo is an existing temp folder; W.start patched to return {"id": "n"}
    self.assertEqual(self.post("/api/sessions/new", {"workspace": "x", "folder": repo, "prompt": "hi"})[:2], (200, {"id": "n"}))
    self.assertEqual(self.post("/api/sessions/new", {"project": "x", "folder": repo, "prompt": "hi"})[:2], (200, {"id": "n"}))
```

Old names through the real `Handler`, with the old config shape, by reusing the route matrix:

```python
class OldRoutes(test_hardening.RouteMatrix):
    """The same matrix under the old names: every old route answers our Host and refuses any other."""
    def config(self):
        new = super().config()
        return {**{k: v for k, v in new.items() if k != "projects"},
                "workspaces": [{**{k: v for k, v in p.items() if k != "repos"}, "folders": p["repos"]} for p in new["projects"]]}
    def routes(self):
        return [("GET", "/api/workspaces", None, 200), ("GET", "/api/workspaces/w/tasks", None, 200),
                ("GET", "/api/workspaces/other/tasks", None, 200), ("GET", "/api/workspaces/w/work", None, 200),
                ("GET", "/workspace?id=w", None, 302), ("GET", "/workspace.html", None, 302),
                ("POST", "/api/workspaces/w/tasks", {"add": "x"}, 200),
                ("POST", "/api/workspaces/w/tests/run", {"repo": self.repo}, 200),
                ("POST", "/api/sessions/new", {"workspace": "w", "folder": self.repo, "prompt": "hi"}, 200)]
```

Import the module, not the class (`import test_hardening`), so the matrix itself is not collected twice.

Notes cards, in `tests/test_project_management.py` (no old word needed):

```python
def test_notes_cards_are_found_under_either_key(self):
    card = {"name": "Reel Brain", "summary": "s"}
    self.assertEqual([n["name"] for n in C.project_notes({"projects": [card], "project_notes": []})], ["Reel Brain"])
    self.assertEqual([n["name"] for n in C.project_notes({"projects": [{"id": "x", "name": "X", "repos": []}], "project_notes": [card]})], ["Reel Brain"])
    self.assertEqual(C.project_notes({"projects": [{"id": "x", "name": "X", "repos": []}], "project_notes": []}), [])
    self.assertNotIn("work" + "spaces", C.DEFAULT_CONFIG)
```

Run: `python3 -m unittest tests.test_old_names 2>&1 | tail -5`
Expected: FAIL (no `old_names`).

- [ ] **Step 3: Implement**

- `centre.py`: rename as the Interfaces block says. Every string, name, docstring and comment loses the old word. The parameter of `build_index` that held the snapshot's folder rows is named `folders`. `_list(handler, cfg)` becomes `listing(cfg)` plus the send in `handle`.
- `old_names.py`: the five functions and two constants. Its docstring says: these are the names from before 2026-11-11, kept as aliases; see "Names during the move" in the core spec; this is the only code that may say the old word.
- `__init__.py`: as the Interfaces block says. It may import `settings_api` for `_send`, as `centre.py` does.
- `collect.py`: the defaults, `project_notes`, `PAGES`, the phase text, and the two comments that say the old word.
- `git mv dashboard/workspace.html dashboard/project.html`, content unchanged.
- `jarvis.py`: the table header in `_ask_context`.

- [ ] **Step 4: Run the whole suite**

Run: `python3 -W error -X dev -m unittest tests.test_old_names tests.test_projects tests.test_project_management` then `python3 -m unittest discover -s tests`
Expected: OK with clean output, then all tests OK. `grep -rn -i "work""space" collector tests | grep -v old_names` prints nothing. `OldRoutes` proves the old page addresses redirect and the old routes answer through the real `Handler`.

- [ ] **Step 5: Commit**

```bash
git add -A collector tests
git commit -m "Project management: the code says project and unassigned; old names live in one alias layer"
```

---

### Task 4: The pages and the Jarvis agent use the new names

**Files:**
- Modify: `dashboard/project.html`, `dashboard/home.html`, `dashboard/shell.html`, `agents/jarvis.md`, `DESIGN.md` (the one line that lists the page files), `tests/test_hardening.py`

Read `DESIGN.md` first. No layout, colour or component changes: only names and visible words.

**Interfaces:**
- Consumes: the new routes and keys of Task 3, and the page file it renamed.

- [ ] **Step 1: Write the failing tests** (`tests/test_hardening.py`)

Add to `IndexFailure.test_index_error_stays_on_the_idle_status` the line `self.assertIn("core_error", (REPO / "dashboard" / "shell.html").read_text())`. Add:

```python
class PageNames(unittest.TestCase):
    def test_pages_and_the_agent_use_the_new_names(self):
        old = "work" + "space"
        for name in ("dashboard/home.html", "dashboard/project.html", "dashboard/shell.html", "agents/jarvis.md", "DESIGN.md"):
            text = (REPO / name).read_text()
            self.assertNotIn(old, text.lower(), name)
        for name in ("home.html", "project.html", "shell.html"):
            self.assertIn("/api/projects", (REPO / "dashboard" / name).read_text(), name)
        self.assertFalse((REPO / "dashboard" / (old + ".html")).exists())
        shell = (REPO / "dashboard" / "shell.html").read_text()
        self.assertIn('href="/project?id=', shell)
        self.assertIn("<h2>Projects</h2>", shell)
        self.assertIn("/api/projects", (REPO / "agents" / "jarvis.md").read_text())
        self.assertIn("projects.json", (REPO / "agents" / "jarvis.md").read_text())
```

Run: `python3 -m unittest tests.test_hardening 2>&1 | tail -5`
Expected: FAIL.

- [ ] **Step 2: Change the pages**

- `project.html`: fetches `/api/projects`, `/api/projects/<id>/tasks`, `/work`, `/tests/run`; reads `data.projects`; posts `{ project: id, folder, prompt }`. Visible text: title "Stellaxis Project", heading "Project", "Couldn't load projects. Retrying.", aria-label "Project views", "No plans in this project's repos.", "No routines for this project.", "No such project.".
- `home.html`: fetches `/api/projects`, reads `d.projects`. Visible text: "Projects", "Loading projects…", "Couldn't load projects. Retrying.". The spoken command strips a trailing "project" where it stripped the old word. Comments use the new word.
- `shell.html`: the group heading is "Projects"; links are `/project?id=…` and the current-page test compares with `/project`; fetches `/api/projects`, reads `d.projects`. The status line: with `index_error` it says "Project index failed: …"; else with `core_error` it says "Store failed: …" in the same state ("down") and with the same 120-character cut.
- `agents/jarvis.md`: the routes are `/api/projects`, `/api/projects/<id>/tasks`, `/api/projects/<id>/work`; the fallback file is `~/.stellaxis/projects.json`; the group of folders in no project is "unassigned"; the word is "project" throughout. Where the text listed "workspaces, projects, Claude sessions", say "projects, repos, Claude sessions".
- `DESIGN.md` names `project.html`.

- [ ] **Step 3: Run the whole suite**

Run: `python3 -W error -X dev -m unittest tests.test_hardening tests.test_old_names` then `python3 -m unittest discover -s tests`
Expected: OK with clean output, then all tests OK. `OldRoutes` passes unedited: the old page addresses still redirect.

- [ ] **Step 4: Commit**

```bash
git add -A dashboard agents DESIGN.md tests/test_hardening.py
git commit -m "Pages and the Jarvis agent: project, not the old word; the sidebar names a store error"
```

---

### Task 5: The config rename, as an action with a dry run

**Files:**
- Modify: `collector/components/project_management/old_names.py`, `collector/components/project_management/__init__.py`
- Test: `tests/test_old_names.py`

**Interfaces:**
- Consumes: `Ctx.act`, `Component.undo`, `Core.undo_handlers` (Task 1); `centre._home()`, `centre._TASK_LOCK`.
- Produces:

```python
# old_names.py
def changes(user: dict) -> list[str]
# What to_new would change in the content of config.json, one line each, in this order and wording:
#   'config.json: "workspaces" becomes "projects" (N entries)'
#   'config.json: "folders" becomes "repos" in N entries'
#   'config.json: "projects" becomes "project_notes" (N notes cards)'
# [] when the file already uses the new keys. A config with no "workspaces" key whose "projects" holds entries
# without an id (and no "project_notes" key) gives the third line only.
def to_new(user: dict) -> dict     # the content with the new keys, key order kept; never changes its argument
def to_old(user: dict) -> dict     # the reverse of to_new, for undo; key order kept

# __init__.py
def new_names(plan, ctx) -> bool                 # the action "new_names"
def _old_config_keys(entry, apply, out) -> bool  # the undo step "project_management.old_config_keys"
COMPONENT = Component(..., actions={"new_names": new_names}, undo={"project_management.old_config_keys": _old_config_keys})
# Routes, answered by the wrapper before centre.handle:
#   GET  /api/projects/new-names                  -> 200, ctx.act("new_names")            (the dry run)
#   POST /api/projects/new-names {"apply": bool}  -> 200, ctx.act("new_names", {}, apply) (400 when "apply" is not a boolean)
```

What `new_names` does, in order. `f = centre._home() / "config.json"`.
1. When `f` holds a JSON object and `changes(...)` is not empty: `plan.say` each line; `plan.backup_json(f)`; add the manifest entry `{"op": "project_management.old_config_keys", "file": str(f)}`; when applying, write `to_new(...)` with `core.util.write_json(f, …, indent=2)`.
2. With `old = tasks/other.md` and `new = tasks/unassigned.md` under `centre._home()`: when `old` exists and `new` does not, `plan.say(f"move {short_path(old)} -> {short_path(new)}")`, add `{"op": "move", "from": str(old), "to": str(new)}`, and when applying rename it while holding `centre._TASK_LOCK`.
3. When nothing was said: `plan.say("nothing to rename: the new names are already in use")`.
4. Return True.

The undo step says `put the old keys back in <short path>` (with the dry-run prefix the core's own steps use) and, when applying, writes `to_old` of the file as it is then.

- [ ] **Step 1: Write the failing tests** (`tests/test_old_names.py`)

In `NamesTest`:

```python
def test_the_rename_of_the_config_content(self):
    old = {"interval": "1m", "workspaces": [{"id": "x", "name": "X", "folders": ["~/code/x"], "tests": {"~/code/x": ["true"]}}, {"id": "y", "folders": []}],
           "projects": [{"name": "A card"}], "port": 7777}
    new = O.to_new(old)
    self.assertEqual(list(new), ["interval", "projects", "project_notes", "port"])
    self.assertEqual(new["projects"], [{"id": "x", "name": "X", "repos": ["~/code/x"], "tests": {"~/code/x": ["true"]}}, {"id": "y", "repos": []}])
    self.assertEqual(new["project_notes"], [{"name": "A card"}])
    self.assertEqual(O.to_old(new), old)
    self.assertEqual(list(O.to_old(new)), list(old))
    self.assertEqual(O.changes(old), ['config.json: "workspaces" becomes "projects" (2 entries)',
                                      'config.json: "folders" becomes "repos" in 2 entries',
                                      'config.json: "projects" becomes "project_notes" (1 notes cards)'])
    self.assertEqual(O.changes(new), [])
    self.assertEqual(O.to_new(new), new)
    self.assertEqual(O.changes({"projects": [{"name": "A card"}]}), ['config.json: "projects" becomes "project_notes" (1 notes cards)'])
    self.assertEqual(O.to_new({"projects": [{"name": "A card"}]}), {"projects": [], "project_notes": [{"name": "A card"}]})
    self.assertEqual(O.changes({}), [])
```

New class `RenameTest`. It uses `test_hardening.fake_home(self, home, claude)` and serves the real `C.Handler` on port 0 (as `RouteMatrix` does, with `C.collect` patched), so every request reads `config.json` again. `config.json` is written with `OLD_CFG` plus `"interval": "1m"` using `indent=4`, and `tasks/other.md` holds `"- [ ] kept\n"`. `self.listed()` runs `C.write_index(C.load_config(), {"sessions": [ROW], "projects": []})` (with `W.live` and `W.app_records` patched, `ROW` a session in `~/code/x`), asserts it returned `None`, and returns the project ids of `GET /api/projects`:

```python
def test_the_dry_run_shows_old_and_new_and_changes_nothing(self):
    before = self.cfg_file.read_bytes()
    status, body, _ = self.get("/api/projects/new-names")
    self.assertEqual((status, body["applied"], body["ok"], body["manifest"]), (200, False, True, None))
    self.assertEqual(body["steps"][:3], ['config.json: "workspaces" becomes "projects" (1 entries)',
                                         'config.json: "folders" becomes "repos" in 1 entries',
                                         'config.json: "projects" becomes "project_notes" (1 notes cards)'])
    self.assertTrue(body["steps"][3].startswith("move ") and body["steps"][3].endswith("unassigned.md"))
    self.assertEqual(self.post("/api/projects/new-names", {"apply": False})[1]["applied"], False)
    self.assertEqual(self.post("/api/projects/new-names", {"apply": "yes"})[0], 400)
    self.assertEqual(self.post("/api/projects/new-names", {})[0], 400)
    self.assertEqual(self.cfg_file.read_bytes(), before)
    self.assertTrue((self.home / "tasks" / "other.md").exists())
    self.assertFalse((self.home / ".claude" / "_archive").exists())

def test_apply_renames_backs_up_and_the_service_reads_the_same_projects(self):
    before = self.cfg_file.read_bytes()
    seen = C.load_config()
    listed = self.listed()
    self.assertEqual(listed, ["x", "unassigned"])
    status, body, _ = self.post("/api/projects/new-names", {"apply": True})
    self.assertEqual((status, body["applied"], body["ok"]), (200, True, True))
    user = json.loads(self.cfg_file.read_text())
    self.assertEqual(user["projects"], [{"id": "x", "name": "X", "repos": ["~/code/x"], "routines": ["r"]}])
    self.assertEqual(user["project_notes"], [{"name": "A card"}])
    self.assertNotIn("workspaces", user)
    self.assertIn(self.home.parent, Path(body["manifest"]).parents)          # under the temp folder, never the real ~/.claude
    man = json.loads(Path(body["manifest"]).read_text())
    self.assertEqual([e["op"] for e in man["entries"]], ["json_backup", "project_management.old_config_keys", "move"])
    self.assertEqual(Path(man["entries"][0]["backup"]).read_bytes(), before)
    self.assertEqual((self.home / "tasks" / "unassigned.md").read_text(), "- [ ] kept\n")
    self.assertEqual(self.listed(), listed)                      # the new keys are read as the old ones were
    self.assertEqual(C.project_notes(C.load_config()), C.project_notes(seen))
    self.assertEqual([n["name"] for n in C.project_notes(seen)], ["A card"])
    [row] = [json.loads(l) for l in (self.home / "actions.jsonl").read_text().splitlines()]
    self.assertEqual((row["component"], row["action"], row["ok"]), ("project_management", "new_names", True))
    again = self.post("/api/projects/new-names", {"apply": True})[1]
    self.assertEqual((again["steps"], again["manifest"]), (["nothing to rename: the new names are already in use"], None))

def test_prune_restore_undoes_it_and_keeps_a_later_edit(self):
    name = Path(self.post("/api/projects/new-names", {"apply": True})[1]["manifest"]).parent.name
    user = json.loads(self.cfg_file.read_text())
    self.cfg_file.write_text(json.dumps({**user, "interval": "5m"}))
    out = self.prune("restore", name, "--apply")       # prune.py as a child process, on the fake home
    self.assertIn("put the old keys back in", out)
    back = json.loads(self.cfg_file.read_text())
    self.assertEqual((back["workspaces"], back["projects"], back["interval"]), (OLD_CFG["workspaces"], OLD_CFG["projects"], "5m"))
    self.assertNotIn("project_notes", back)
    self.assertEqual((self.home / "tasks" / "other.md").read_text(), "- [ ] kept\n")
    self.assertFalse((self.home / "tasks" / "unassigned.md").exists())
```

`self.prune` runs `collector/prune.py` the way `tests/test_relocate.py` does (child process, `HOME`, `STELLAXIS_HOME` and `CLAUDE_CONFIG_DIR` set to this test's folders; the fake home needs whatever `C.collect` needs to finish, as in that file).

- [ ] **Step 2: Run them to see them fail**

Run: `python3 -m unittest tests.test_old_names 2>&1 | tail -5`
Expected: FAIL (`old_names` has no `to_new`; the route answers 404).

- [ ] **Step 3: Implement** as the Interfaces block says.

- [ ] **Step 4: Run the file, then the whole suite**

Run: `python3 -W error -X dev -m unittest tests.test_old_names` then `python3 -m unittest discover -s tests`
Expected: OK with clean output, then all tests OK.

- [ ] **Step 5: Commit**

```bash
git add collector/components/project_management tests/test_old_names.py
git commit -m "Project management: the config moves to the new keys only when the owner applies it, with a dry run, a backup and an undo"
```

---

### Task 6: Check 5, and the documents

**Files:**
- Modify: `tests/test_project_management.py`, `CLAUDE.md`, `CONTEXT.md` (its note about the word, only)

- [ ] **Step 1: Write the test of check 5** (`tests/test_project_management.py`)

```python
class OldWordTest(unittest.TestCase):
    """Check 5 of the core spec: the old word remains only where "Names during the move" allows."""
    WORD = "work" + "space"
    ALLOWED = {"collector/components/project_management/old_names.py", "tests/test_old_names.py", "CONTEXT.md",
               "README.md"}   # README.md until step 3 (check 6 of the spec)

    def test_the_old_word_remains_only_in_the_alias_layer_and_the_documents(self):
        tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertGreater(len(tracked), 50)
        found = []
        for name in tracked:
            if name.startswith("docs/") or name in self.ALLOWED:
                continue
            if self.WORD in name.lower():
                found.append(name)
                continue
            try:
                text = (REPO / name).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            found += [f"{name}:{i}" for i, line in enumerate(text.splitlines(), 1)
                      if self.WORD in line.lower().replace("nexus-" + self.WORD, "")]
        self.assertEqual(found, [])
```

Run: `python3 -m unittest tests.test_project_management.OldWordTest`
Expected: FAIL, listing lines of `CLAUDE.md` only. If it lists anything else, fix that file in the way its task said.

- [ ] **Step 2: `CLAUDE.md`**

Rewrite the parts that describe what changed, in the file's own style and with the words of `CONTEXT.md`:

- The index is `projects.json`; the pages are Home and Project (`/project?id=<id>` is `project.html`).
- `collector/components/project_management/`: `centre.py` (new names only), `old_names.py` (the alias layer: what it maps, and that the old names stay until a later step removes them), `__init__.py` (reader, routes, the four offers, the action `new_names` and its undo step). There is no alias module any more. Nothing outside the core imports the component; Jarvis reads it with `Core.read`.
- The core: `Ctx.act`, `Core.read`, `Component.undo` and its name rule, `restore` refusing an unknown step, the unique manifest folder, actions running with the store down.
- Config keys: `projects`, a list of `{"id", "name", "repos": [path], "tests": {repo: argv}, "routines": [name]}`, and `project_notes`. The old keys are read until the owner applies the rename: `GET /api/projects/new-names` shows the dry run, `POST` with `{"apply": true}` applies it, `prune.py restore <stamp> --apply` undoes it.
- Files under `$STELLAXIS_HOME`: `tasks/<project id>.md`; the unassigned list is `tasks/unassigned.md`.
- API routes: the `/api/projects…` list, `POST /api/sessions/new` with `{"project", "folder", "prompt"}`, and one sentence that every old name still answers through the alias layer.
- Dashboard section: the routes and file names.

- [ ] **Step 3: `CONTEXT.md`**

Only the first line under "Flagged ambiguities" changes. It now says: resolved, the word is **project**; the old word remains only as old names that still answer (old routes, the old page address, the old index file) and in `config.json` until the owner applies the rename.

- [ ] **Step 4: Run the whole suite**

Run: `python3 -W error -X dev -m unittest tests.test_project_management` then `python3 -m unittest discover -s tests`
Expected: OK with clean output, then all tests OK.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md CONTEXT.md tests/test_project_management.py
git commit -m "Docs and check 5: the old word remains only in the alias layer and the documents"
```

---

## What this plan leaves for later plans

- **Step 3:** records of Project management in the store, the shared transcript helper, `relocate` as an action of this component, Claude management, `README.md`.
- **The owner applies the config rename** when he chooses. Until then the service reads the old keys.
- **Removing the alias layer** (`old_names.py`, `tests/test_old_names.py`, `workspaces.json`, `centre.index_fallback`, the `tasks/other.md` fallback in `centre._task_file`, the id-less fallback in `collect.project_notes`, and the action `new_names` with its undo step): a later step, after the installed copy of the Jarvis agent uses the new names and the config rename is applied.
