# Stellaxis Core, Step 3A (What the Core Still Lacks) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the core what steps 2B and 5 found missing, so that step 3B can move Claude management into a component: public helpers, the folders on `Ctx`, offers that cannot break a caller, a rule for changing a table, rounds in a pass, and the shared transcript helper.

**Architecture:** Only `collector/core/` grows. The two components and the ways in change only where they used private or host names. `collect.py` still builds the snapshot in this plan; it only reads transcripts through the new helper, which proves the helper against the reference test before anything else moves.

**Tech Stack:** Python 3 stdlib, `unittest`, `sqlite3`.

**Spec:** `docs/superpowers/specs/2026-11-11-core-architecture-design.md` (words follow `CONTEXT.md`): "The core", "The abstract component", "Left to the builder". Also "Found while building step 2B, for the step 3 plan" in `2026-11-11-core-step-2b-project-names.md` and "Found while building step 5" in `2026-11-11-core-step-5-memory-read-side.md`.

## Global Constraints

- Python 3 standard library only. No packages, no build step.
- All tests pass after every task (321 before Task 1): `python3 -X dev -m unittest discover -s tests`. The output is clean: no warnings, no stray prints.
- `tests/test_same_output.py` passes after every task. It compares the present code with what commit `dfb604b` gave, on one fake home. Never edit `tests/same_output_expected.json` or `tests/same_output_harness.py` to make it pass. If a difference is wanted, stop and report it.
- Every existing page, API route, the desktop MCP tool and the artifact path of `index.html` and `hierarchy.html` answer as before. `prune.py` keeps its exact command line and output. Item ids keep their form.
- The core (`collector/core/`) imports nothing outside itself. Every file in it starts with `from __future__ import annotations`.
- A component imports only the standard library, `core` and its own folder.
- `cache.json` keeps its format: the keys `files`, `ids`, `walk`, `probes`, the same keys in the same order in each file's state, and `"v": "0.2.2"`. The running service must go on from the cache it has.
- Never use `git stash`. Never run `launchctl` or `collect.py serve`. Never touch port 7777. Never change the real `~/.stellaxis` or `~/.claude`: tests use a fake home (`STELLAXIS_HOME`, `HOME`, `CLAUDE_CONFIG_DIR`). Work only in this worktree. Do not push. Do not commit on `core-architecture`.
- Never create a temp component folder named `project_management`, `memory_management` or `claude_management` in a test.
- In tests, get a component's module with `core.component.import_folder(REPO / "collector" / "components" / "<name>")`, never by another import. A test that applies an action uses `fake_home` from `tests/test_hardening.py`.
- The old word for "project" may be written only in `collector/components/project_management/old_names.py`, `tests/test_old_names.py`, `docs/` and `CONTEXT.md`. Anywhere else a test builds it from two parts.
- End each commit message with a blank line and `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.

## Decisions made by the writer (my calls), with the cost if wrong

These hold for 3A and 3B. The owner has not confirmed them.

1. **The search for folders (`discover_projects`) moves to Project management. Claude management reads the result.** This follows the spec's table ("the list of repos, to find the `.claude` folder of each repo"). Cost: with Project management removed, the snapshot lists no items of single repos. Moving it back is one file.
2. **A pass runs in rounds.** Round one runs the first reader of every component, round two the second, and so on. Inside a round the order is the component name. So a reader in round two sees what every component wrote in round one. Cost: the round of a reader is its place in a list, which a reader of the code must know.
3. **The shared transcript helper lives in the core** (`core/transcripts.py`). It keeps `cache.json` in the format of today. Cost: the helper and Claude management share one flat state per file.
4. **`cache.json` stays a file.** It holds the place reached in each transcript, the parse state, the folder walk and the probe results. These are not records. Cost: one more file beside the store.
5. **The plain facts of a session (id, folder, branch, first and last time) belong to the helper.** Both components take them from it. Cost: none known.
6. **`snapshot.json` and the store rows are written in the same pass from the same result.** The file is not read back out of the store. `projects.json` is written from the store rows while the store works. The routes keep reading the files. Cost: when the files stop, the routes must read the store.
7. **Task lists, approvals and test results of Project management stay files.** Cost: a later step moves them.
8. **`history.jsonl` stays a file.** Cost: none.
9. **`index_error` in `status.json` keeps its name** and holds the errors of every component as one string (`name: error; name: error`). A component folder that is removed is not an error. Cost: the name is no longer exact; a removed component shows only in `GET /api/core`.
10. **A settings save runs through the core.** Each save writes a manifest, so `prune.py restore` can undo it. The saves show in `prune.py archives`, and `restore last` can pick one. The `.bak-<stamp>` file beside the settings file stays. Cost: more archive folders; "last" means a settings save when that was the last change.
11. **The data of a settings save never reaches the action log.** An action names the arguments the log must not hold. Cost: the log line of a save says less.
12. **A new relocation writes the step name `project_management.relocate`.** Old archives with the step `relocate` still restore, through one alias in `prune.py`. Cost: old code cannot undo a move made by new code.
13. **A way in may hand an action its own plan**, so `prune.py` prints exactly what it printed before. Cost: none.
14. **An undo handler gets the `ctx` as a fourth argument.** Cost: the one handler that exists changes its signature.
15. **A component must work with an empty config.** The defaults stay in `collect.DEFAULT_CONFIG`. With a key missing, a component does the least (no folder walk, no prices). Cost: only tests see the fallbacks, so they can drift from the defaults.
16. **The readers of Claude management and Project management run only in a collection** (the pass inputs hold the key `collector`). Cost: a bare `run_pass` reads nothing, as today for the index.
17. **One clock, `core.util.now()`.** It obeys `STELLAXIS_NOW`, for tests. Cost: one test variable in product code, like `STELLAXIS_DESKTOP_RUNNING`.
18. **A rule for changing a table:** `Component.changes` and the table `core_schema`. Cost: one bookkeeping table that the core owns.
19. **`Ctx.service` says "this is the service". No helper for slow work is built, and Memory management does not change.** Cost: Memory management keeps its own thread and its first-pass rule.
20. **The command centre (`centre.py`) takes its two folders from a context variable** that the component sets from `ctx` at every entry. Cost: a thread started inside the centre must be handed its paths.
21. **`collector/settings_api.py` is deleted in 3B.** `collect.py`, `prune.py` and `mcp_server.py` stay as ways in. Cost: a script outside the repo that imports it breaks; none is known.
22. **An offer that raises gives an empty list.** The error shows in `GET /api/core` under `offer_errors`. Cost: a caller cannot tell "nothing" from "failed" without looking there.
23. **The Reel Brain summary and the notes cards reach the snapshot as pass inputs from `collect.py`.** Claude management does not read their sources. Cost: none.
24. **Each session row in the store also holds its cost for each hour.** Step 4 needs it for a week that starts in the middle of a session. Cost: a larger row.
25. **Still open, not fixed here:** `server.handle` writes a 500 even when the component already sent headers; `_prompt` and `MAX_PROMPT` are in two places; `purge` and `archives` stay inside `prune.py`.

## Review Focus

1. A transcript that shrinks, is deleted, or ends in half a line: it is read again from the start, dropped, or left until the line is whole. Tests in Task 4.
2. A component with a transcript consumer is removed and comes back: the files it missed are read again from the start, the others are not. Tests in Task 4.
3. A store made by older code gets a change: it is applied one time, also when the store had no version row. Tests in Task 3.
4. An offer raises while Jarvis or another component asks: the caller gets an empty list and still answers. Tests in Task 2.
5. A reader raises in round one: round two still runs, for every component. Test in Task 4.

## File Structure

| File | Responsibility |
|---|---|
| `collector/core/util.py` | Adds `now()`. |
| `collector/core/server.py` | Adds `ApiError`, `read_body`, `send_json`, `MAX_BODY`. |
| `collector/core/undo.py` | `pre` is public; `Plan.note`, `Plan.notes`, `Plan.result`. |
| `collector/core/component.py` | `Component.changes`, `Component.lines`; the name `core` is refused. |
| `collector/core/store.py` | `ensure(name, records, changes)` and `core_schema`. |
| `collector/core/transcripts.py` | New. The shared transcript helper. |
| `collector/core/app.py` | Folders and `service` on `Ctx`; safe offers; rounds; the pass cache; `ctx.transcripts()`; `run_action(plan=)`. |
| `collector/settings_api.py` | Takes `ApiError`, `read_body`, `_send` from the core. |
| `collector/components/project_management/` | Imports only the core; folders from `ctx`. |
| `collector/unplaced/jarvis.py` | Imports the helpers from the core. |
| `collector/collect.py` | Reads transcripts through the helper; one clock; tells the core its folders. |
| `collector/prune.py` | `restore` passes the config to `undo_handlers`. |
| `README.md` | Two stale lines corrected (the full rewrite is in 3B). |

---

### Task 1: Public helpers, one clock, and components that import only the core

**Files:**
- Modify: `collector/core/util.py`, `collector/core/server.py`, `collector/core/undo.py`, `collector/settings_api.py`, `collector/components/project_management/__init__.py`, `collector/components/project_management/centre.py`, `collector/unplaced/jarvis.py`, `collector/collect.py`, `collector/components/README.md`, `README.md`
- Test: `tests/test_core.py`, `tests/test_project_management.py`

**Interfaces:**
- Produces: `core.util.now() -> datetime` (UTC, aware). `core.server.ApiError(status, msg, **extra)`, `core.server.read_body(handler) -> dict`, `core.server.send_json(handler, status, obj) -> None`, `core.server.MAX_BODY = 2_000_000`. `core.undo.pre(apply) -> str`. `Plan.note(msg)`, `Plan.notes: list`, `Plan.result = None`.

- [ ] **Step 1: Write the failing tests in `tests/test_core.py`**

```python
class UtilTest(unittest.TestCase):
    def test_the_clock_can_be_fixed(self):
        from core import util
        with mock.patch.dict(os.environ, {"STELLAXIS_NOW": "2026-11-11T12:00:00Z"}):
            self.assertEqual(util.now(), dt.datetime(2026, 11, 11, 12, tzinfo=dt.timezone.utc))
            self.assertEqual(util.now_iso(), "2026-11-11T12:00:00Z")
        with mock.patch.dict(os.environ):
            os.environ.pop("STELLAXIS_NOW", None)
            self.assertLess(abs((util.now() - dt.datetime.now(dt.timezone.utc)).total_seconds()), 5)
```

In `UndoTest`:

```python
    def test_a_note_is_printed_plain_and_kept(self):
        said = []
        plan = undo.Plan(False, "t", self.arch, out=said.append)
        plan.note("  skip x: not found")
        self.assertEqual((said, plan.notes, plan.steps, plan.result), (["  skip x: not found"], ["  skip x: not found"], [], None))
        self.assertEqual((undo.pre(True), undo.pre(False)), ("  ", "  [dry-run] "))
```

In `ServeTest`, a temp component whose `handle` answers `POST /api/alpha/echo` with `send_json(handler, 200, read_body(handler))` and turns `ApiError` into `send_json(handler, e.status, {"error": e.msg, **e.extra})`. Assert: a JSON object comes back with 200; `Content-Type: text/plain` gives 415 and `{"error": "send application/json"}`; the body `[1]` gives 400 and `"request body must be an object"`; the body `{` gives 400; a `Content-Length` over `MAX_BODY` gives 413.

In `WiringTest`, replace `test_host_ok_has_one_home` by:

```python
    def test_the_http_helpers_have_one_home(self):
        import settings_api
        self.assertIs(settings_api.host_ok, server.host_ok)
        self.assertIs(settings_api.ApiError, server.ApiError)
        self.assertIs(settings_api.read_body, server.read_body)
        self.assertIs(settings_api._send, server.send_json)
```

In `tests/test_project_management.py`, replace `IndependenceTest` by:

```python
class IndependenceTest(unittest.TestCase):
    """Check 3 of the core spec: a component reaches another one only through the core."""
    def files(self):
        out = [f for f in COMPS.rglob("*.py")]
        self.assertTrue(out)
        return out

    def test_a_component_imports_only_the_standard_library_the_core_and_itself(self):
        ok = set(sys.stdlib_module_names) | {"core"}
        for f in self.files():
            src = f.read_text()
            self.assertNotIn("import_folder", src, f.name)
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                    if (node.module or "").split(".")[0] == "core":
                        for a in node.names:
                            self.assertFalse(a.name.startswith("_"), f"{f.relative_to(REPO)} imports the private name {a.name}")
                elif isinstance(node, ast.ImportFrom):
                    self.assertEqual(node.level, 1, f"{f.relative_to(REPO)} imports from above its own folder")
                    continue
                else:
                    continue
                for n in names:
                    self.assertIn(n.split(".")[0], ok, f"{f.relative_to(REPO)} imports {n}")
                    self.assertNotEqual(n.split(".")[0], "importlib", f"{f.relative_to(REPO)} imports importlib")

    def test_no_component_names_the_tables_or_the_module_of_another(self):
        names = sorted(p.name for p in COMPS.iterdir() if (p / "__init__.py").is_file())
        self.assertGreaterEqual(len(names), 2)
        for f in self.files():
            mine, src = f.relative_to(COMPS).parts[0], f.read_text()
            for other in names:
                if other != mine:
                    self.assertNotIn(other + "_", src, f"{f.relative_to(REPO)} names a table of {other}")
                    self.assertNotIn("stx_" + other, src, f"{f.relative_to(REPO)} names the module of {other}")
```

In `OldWordTest`, remove `"README.md"` (and its comment) from `ALLOWED`.

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -m unittest tests.test_core tests.test_project_management 2>&1 | tail -20`
Expected: FAIL (`util` has no `now`, `Plan` has no `note`, `settings_api` is imported by a component, `README.md` holds the old word).

- [ ] **Step 3: Implement**

- `core/util.py`: `now()` returns `datetime.fromisoformat(os.environ["STELLAXIS_NOW"].replace("Z", "+00:00"))` when the variable is set and not empty, else `datetime.now(timezone.utc)`. `now_iso()` formats `now()`.
- `core/server.py`: move `ApiError`, `read_body` and `MAX_BODY` here from `settings_api.py`, unchanged. Rename `_json` to `send_json` (same body) and use it inside the file.
- `core/undo.py`: rename `_pre` to `pre` everywhere in the file. `Plan.__init__` sets `self.notes = []` and `self.result = None`. `Plan.note(msg)` appends to `notes` and calls `self.out(msg)` with no prefix.
- `settings_api.py`: delete its own `ApiError`, `read_body`, `_send`, `MAX_BODY`; add `from core.server import ApiError, MAX_BODY, host_ok, read_body, send_json as _send  # noqa: F401`.
- `project_management/__init__.py` and `centre.py`: no `import settings_api`. Use `from core.server import ApiError, host_ok, read_body, send_json` and `from core.undo import pre`. `centre.ApiError` stays a name of the module (tests use it).
- `unplaced/jarvis.py`: the same four names from `core.server`; no `import settings_api`.
- `collect.py`: `now_utc()` returns `core.util.now()`. `Handler` calls `core_server.host_ok`. Keep `import settings_api` (its `handle` is still used; tests use `C.settings_api`).
- `centre.py`: every `datetime.now(timezone.utc)` becomes `util.now()` (`from core import util`).
- `README.md`: in the Layout block, the line for the command centre becomes `collector/components/project_management/  command centre: project index, sessions, tasks, approvals, plans, tests` and the page line becomes `dashboard/project.html  Project: sessions, tasks, plans, routines, tests (localhost only)`.
- `collector/components/README.md`: replace the last line by "A route reads a JSON body with `core.server.read_body`, which refuses other content types, answers with `core.server.send_json`, and raises `core.server.ApiError` for a refusal." Add: "A component imports only the standard library, `core` and its own folder. It never names another component's tables or module."

- [ ] **Step 4: Run the whole suite**

Run: `python3 -X dev -m unittest discover -s tests 2>&1 | tail -4`
Expected: `OK`, nothing else printed.

- [ ] **Step 5: Commit**

```bash
git add -A collector tests README.md
git commit -m "Core: public HTTP helpers, one clock, notes on a plan; components import only the core"
```

---

### Task 2: The folders on `Ctx`, offers that cannot break a caller, undo with a `ctx`, a plan from the way in

**Files:**
- Modify: `collector/core/app.py`, `collector/components/project_management/__init__.py`, `collector/components/project_management/centre.py`, `collector/collect.py`, `collector/prune.py`, `collector/components/README.md`
- Test: `tests/test_core.py`, `tests/test_project_management.py`, `tests/test_jarvis.py`, `tests/test_old_names.py`

**Interfaces:**
- Produces:
  - `Core(home, claude_dir, components_dir, claude_json=None)`; `core.claude_json` (default `claude_dir / ".claude.json"`); `core.service = False`; `core.on_status = None`; `core.offer_errors: dict[str, str]` keyed `"owner.offer"`.
  - `Ctx.claude_dir`, `Ctx.claude_json`, `Ctx.service`, `Ctx.status(phase: str, force: bool = True, **detail) -> None` (calls `core.on_status` when it is set, never raises).
  - `Core.read` and `Ctx.read` return `[]` when the offer raises, and record the error. A good call removes the record.
  - `Core.undo_handlers(cfg: dict | None = None) -> dict[str, (entry, apply, out) -> bool]`. A component's handler has the signature `(entry, apply, out, ctx)`.
  - `Core.run_action(component, action, args, apply, cfg, plan=None) -> {"applied", "ok", "steps", "notes", "result", "manifest"}`. `Ctx.act(action, args=None, apply=False, plan=None)`. An action function may carry `private = ("name", ...)`: those arguments are left out of `actions.jsonl`.
  - `centre.at(ctx)`: a context manager. Inside it `centre._home()` and `centre._claude_dir()` give `ctx.home` and `ctx.claude_dir`. Outside it they read the environment as before.

- [ ] **Step 1: Write the failing tests**

In `tests/test_core.py`, `CoreTest`:

```python
    def test_a_component_sees_the_folders_and_the_service_flag(self):
        put(self.comps, "alpha", "from core.component import Component\nSEEN = []\n"
            "def r(ctx):\n    SEEN.append((ctx.home, ctx.claude_dir, ctx.claude_json, ctx.service))\n    ctx.status('working', force=False, path='x')\n"
            "COMPONENT = Component(name='alpha', readers=[r])\n")
        core = Core(self.home, self.home / ".claude", self.comps)
        said = []
        core.on_status = lambda phase, force=True, **detail: said.append((phase, force, detail))
        core.run_pass({})
        core.service = True
        core.run_pass({})
        seen = sys.modules["stx_alpha"].SEEN
        self.assertEqual(seen[0], (self.home, self.home / ".claude", self.home / ".claude" / ".claude.json", False))
        self.assertTrue(seen[1][3])
        self.assertEqual(said[0], ("working", False, {"path": "x"}))
        self.assertEqual(Core(self.home, self.home / ".claude", self.comps, claude_json=self.home / "cj").claude_json, self.home / "cj")

    def test_an_offer_that_raises_gives_nothing_and_is_reported(self):
        put(self.comps, "alpha", "from core.component import Component\nBAD = [True]\n"
            "def o(ctx):\n    if BAD[0]:\n        raise ValueError('boom')\n    return [1]\n"
            "COMPONENT = Component(name='alpha', offers={'o': o})\n")
        put(self.comps, "beta", "from core.component import Component\nGOT = []\n"
            "def r(ctx):\n    GOT.append(ctx.read('alpha', 'o'))\n"
            "COMPONENT = Component(name='beta', readers=[r], reads=[('alpha', 'o')])\n")
        core = Core(self.home, self.home / ".claude", self.comps)
        self.assertEqual(core.read("alpha", "o", {}), [])
        self.assertEqual(core.run_pass({})["components"]["beta"]["errors"], [])
        self.assertEqual(sys.modules["stx_beta"].GOT, [[]])
        self.assertEqual(core.describe()["offer_errors"], {"alpha.o": "ValueError: boom"})
        sys.modules["stx_alpha"].BAD[0] = False
        self.assertEqual(core.read("alpha", "o", {}), [1])
        self.assertEqual(core.describe()["offer_errors"], {})
```

In `ActionTest`: change `unnote` in `ACT` to `def unnote(e, apply, out, ctx):` and make it also do `out("home " + str(ctx.home))`. Add an action `secret(plan, ctx, path, body)` with `secret.private = ("body",)` that sets `plan.result = {"n": len(body)}`, calls `plan.note("  kept quiet")` and returns True. Tests:

```python
    def test_private_arguments_stay_out_of_the_log_and_the_result_comes_back(self):
        r = self.core.run_action("alpha", "secret", {"path": self.f, "body": "s3cret"}, True, {})
        self.assertEqual((r["result"], r["notes"]), ({"n": 6}, ["  kept quiet"]))
        row = json.loads(self.log.read_text().splitlines()[-1])
        self.assertEqual(row["args"], {"path": self.f})
        self.assertNotIn("s3cret", self.log.read_text())

    def test_a_way_in_can_bring_its_own_plan(self):
        said = []
        plan = undo.Plan(True, "mine", self.home / ".claude" / "_archive", out=said.append)
        r = self.core.run_action("alpha", "touch", {"path": self.f, "keep": True}, True, {}, plan=plan)
        self.assertIn("  create " + self.f, said)
        self.assertTrue(Path(r["manifest"]).parent.name.endswith("-mine"))
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_an_undo_handler_gets_the_ctx(self):
        r = self.core.run_action("alpha", "note", {"path": self.f}, True, {})
        said = []
        undo.restore(Path(r["manifest"]).parent.name, True, self.home / ".claude" / "_archive",
                     handlers=self.core.undo_handlers({"k": 1}), out=said.append)
        self.assertIn("home " + str(self.home), said)
```

Update `test_a_component_undo_step_restores_through_the_core_table` for the new signature (it stays otherwise).

In `tests/test_jarvis.py`: a test that patches the `index` offer of the real component to raise (`mock.patch.dict(comp.offers, {"index": boom})` on the component object inside the `Core` the test builds) and asserts `GET /api/jarvis` still answers 200. Read the file first and follow how its other tests build the core and call the route.

In `tests/test_project_management.py`, replace `test_the_reader_refuses_a_home_that_is_not_the_environments` by:

```python
    def test_the_reader_writes_to_the_cores_home_not_the_environments(self):
        elsewhere = self.home / "elsewhere"
        core = Core(elsewhere, self.home / ".claude", COMPS)
        with mock.patch.object(W, "live", return_value=[]), mock.patch.object(W, "app_records", return_value={}):
            res = core.run_pass(dict(CFG), {"sessions": [ROW], "folders": []})
        self.assertEqual(res["components"]["project_management"]["errors"], [])
        self.assertTrue((elsewhere / "projects.json").is_file())
        self.assertFalse((self.home / "projects.json").exists())

    def test_outside_a_component_call_the_centre_reads_the_environment(self):
        self.assertEqual(W._home(), self.home)
        class Ctx:
            home, claude_dir = self.home / "a", self.home / "b"
        with W.at(Ctx):
            self.assertEqual((W._home(), W._claude_dir()), (self.home / "a", self.home / "b"))
        self.assertEqual(W._claude_dir(), self.home / ".claude")
```

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -m unittest tests.test_core tests.test_project_management tests.test_jarvis 2>&1 | tail -20`
Expected: FAIL.

- [ ] **Step 3: Implement the core part in `core/app.py`** as the Interfaces block says. `Core.read` catches `Exception` only. `describe()` gains the key `offer_errors`. `undo_handlers` binds each handler to `self.ctx(component name, cfg or {})`. In `run_action`, the log row's `args` is `{k: v for k, v in args.items() if k not in getattr(fn, "private", ())}`.

- [ ] **Step 4: Implement the component part**

- `centre.py`: `_PLACES = contextvars.ContextVar("stellaxis_places", default=None)`; `at(ctx)` as a `contextlib.contextmanager` that sets `(Path(ctx.home), Path(ctx.claude_dir))` and resets it in `finally`; `_home()` and `_claude_dir()` return the pair's value when it is set, else what they return today. Check every thread the file starts (`_run_one`, the pool in `repo_states`, anything else): it must get its paths as arguments, never call `_home()` or `_claude_dir()` itself. Fix any that does.
- `project_management/__init__.py`: `index_reader`, `new_names`, `handle` and the four offers each do their work inside `with centre.at(ctx):`. `index_reader` no longer raises for a home that differs. `_old_config_keys` takes `(entry, apply, out, ctx)`.
- `collect.py`: `the_core()` builds `Core(STX_HOME, CLAUDE_DIR, <components>, claude_json=(CLAUDE_DIR / ".claude.json") if os.environ.get("CLAUDE_CONFIG_DIR") else HOME / ".claude.json")` and sets `_core.on_status = status`. `serve()` sets `the_core().service = True`.
- `prune.py`: `restore` calls `C.the_core().undo_handlers(C.load_config())`.
- `collector/components/README.md`: add "`ctx.home`, `ctx.claude_dir` and `ctx.claude_json` are the folders; never read them from the environment." "An undo handler is `(entry, apply, out, ctx)`." "An offer that raises gives the caller an empty list; the error shows in `GET /api/core`." "An action sets `plan.result` to what its route answers, says what it skipped with `plan.note`, and lists in `private` the arguments the log must not hold."

- [ ] **Step 5: Run the whole suite**

Run: `python3 -X dev -m unittest discover -s tests 2>&1 | tail -4`
Expected: `OK`, nothing else printed.

- [ ] **Step 6: Commit**

```bash
git add -A collector tests
git commit -m "Core: folders and the service flag on Ctx, offers that cannot break a caller, undo with a ctx, a plan from the way in"
```

---

### Task 3: A rule for changing a table

**Files:**
- Modify: `collector/core/store.py`, `collector/core/component.py`, `collector/core/app.py`, `collector/components/README.md`
- Test: `tests/test_core.py`

**Interfaces:**
- Produces: `Component.changes: list[list[str]]` (default empty). `Store.ensure(name: str, records: list[str], changes: list[list[str]] = ()) -> None`. The table `core_schema (component TEXT PRIMARY KEY, version INTEGER NOT NULL)`. The loader refuses a component named `core`.

The rule, in words: `records` always describes the present shape. To change a table, edit `records` to the new shape and append one change (a list of statements) that brings an older store to it. An earlier change is never edited or removed.

How `ensure` works, all inside one transaction (`BEGIN IMMEDIATE` … `COMMIT`, `ROLLBACK` on any error, with `isolation_level = None` on that connection):

1. Check every statement before running anything. In `records`: `CREATE TABLE|INDEX IF NOT EXISTS <name>_…` (as today). In a change: that, or `ALTER TABLE <name>_…`, `DROP TABLE IF EXISTS <name>_…`, `DROP INDEX IF EXISTS <name>_…`, `UPDATE <name>_…`, `DELETE FROM <name>_…`, `INSERT INTO <name>_…`. Anything else raises `ValueError`.
2. Create `core_schema` if it is missing. Read the component's version.
3. No version row: the store is new for this component when `sqlite_master` holds no table or index whose name starts with `<name>_`. A new store starts at `len(changes)` (the changes are not run: `records` already is the new shape). A store with tables starts at 0.
4. Run the changes from the version on, in order. Run every statement of `records`. Write `len(changes)` as the version.

- [ ] **Step 1: Write the failing tests in `StoreTest`**

```python
    V1 = ["CREATE TABLE IF NOT EXISTS alpha_t (id TEXT)"]
    V2 = ["CREATE TABLE IF NOT EXISTS alpha_t (id TEXT, more TEXT)"]
    ADD = [["ALTER TABLE alpha_t ADD COLUMN more TEXT"]]

    def cols(self, s):
        with closing(s.connect()) as db:
            return [r[1] for r in db.execute("PRAGMA table_info(alpha_t)")]

    def version(self, s, name="alpha"):
        with closing(s.connect()) as db:
            row = db.execute("SELECT version FROM core_schema WHERE component = ?", (name,)).fetchone()
            return row and row[0]

    def test_a_new_store_gets_the_present_shape_and_no_change_is_run(self):
        s = store.Store(self.path)
        s.ensure("alpha", self.V2, self.ADD)
        self.assertEqual((self.cols(s), self.version(s)), (["id", "more"], 1))

    def test_an_older_store_gets_each_change_one_time(self):
        s = store.Store(self.path)
        s.ensure("alpha", self.V1)
        self.assertEqual(self.version(s), 0)
        s.ensure("alpha", self.V2, self.ADD)
        s.ensure("alpha", self.V2, self.ADD)
        self.assertEqual((self.cols(s), self.version(s)), (["id", "more"], 1))

    def test_a_store_from_before_the_rule_counts_as_version_zero(self):
        s = store.Store(self.path)
        with closing(s.connect()) as db, db:
            db.execute(self.V1[0])
        s.ensure("alpha", self.V2, self.ADD)
        self.assertEqual(self.cols(s), ["id", "more"])

    def test_a_change_that_fails_leaves_the_store_as_it_was(self):
        s = store.Store(self.path)
        s.ensure("alpha", self.V1)
        with self.assertRaises(sqlite3.Error):
            s.ensure("alpha", self.V2, [["ALTER TABLE alpha_t ADD COLUMN more TEXT", "ALTER TABLE alpha_t ADD COLUMN more TEXT"]])
        self.assertEqual((self.cols(s), self.version(s)), (["id"], 0))

    def test_a_change_may_touch_only_its_own_tables(self):
        s = store.Store(self.path)
        s.ensure("beta", ["CREATE TABLE IF NOT EXISTS beta_t (id TEXT)"])
        for bad in ("DROP TABLE IF EXISTS beta_t", "ALTER TABLE beta_t ADD COLUMN x", "DELETE FROM core_schema", "PRAGMA writable_schema = 1"):
            with self.assertRaises(ValueError):
                s.ensure("alpha", self.V1, [[bad]])
        self.assertIsNone(self.version(s, "alpha"))
```

`self.path` is the store path the class already uses; adapt the name to what `StoreTest.setUp` has. In `LoadTest`:

```python
    def test_no_component_may_be_named_core(self):
        put(self.comps, "core", "from core.component import Component\nCOMPONENT = Component(name='core')\n")
        [c] = component.load(self.comps)
        self.assertIn("core", c.error)
```

(The temp folder is named `core`, so `import_folder` makes the module `stx_core`; it does not hide the real `core` package.) In `CoreTest`, one test that a component with `changes` whose statement fails gets `error` set and the other component still loads and runs.

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -m unittest tests.test_core -k StoreTest -k LoadTest 2>&1 | tail -15`
Expected: FAIL (`ensure` takes no third argument).

- [ ] **Step 3: Implement** `Store.ensure` as described, `Component.changes`, the name check in `component._import`, and `self.store.ensure(c.name, c.records, c.changes)` in `Core.__init__`. Add the rule in words to `collector/components/README.md`.

- [ ] **Step 4: Run the whole suite**

Run: `python3 -X dev -m unittest discover -s tests 2>&1 | tail -4`
Expected: `OK`, nothing else printed.

- [ ] **Step 5: Commit**

```bash
git add -A collector tests
git commit -m "Core: a rule for changing a table, with the version of each component kept in the store"
```

---

### Task 4: Rounds, the pass cache and the shared transcript helper

**Files:**
- Create: `collector/core/transcripts.py`
- Modify: `collector/core/component.py`, `collector/core/app.py`, `collector/collect.py`, `collector/components/README.md`
- Test: `tests/test_core.py` (new class `TranscriptsTest`, additions to `CoreTest`), `tests/test_dedupe.py` (unchanged, must pass)

**Interfaces:**
- Produces, in `core/transcripts.py`:
  - `VERSION = "0.2.2"`: the version of a file's state. A state with another `v` is read again from the start.
  - `@dataclass class Consumer: fresh: Callable[[], dict]; line: Callable[[dict, dict, dict, int], None]`. `fresh()` gives the keys the consumer keeps in a file's state, with their start values. `line(state, line, ids, owner)` is called for each new line.
  - `owner(key: str) -> int` (`zlib.crc32` of the path, as `_owner` in `collect.py` today).
  - `read(projects_dir: Path, cache: dict, consumers: list[Consumer]) -> dict`: returns `cache["files"]` (path to state). It is `parse_transcripts` of today with three changes: the folder is an argument; the head of `_line` (the seven lines that set `first_ts`, `last_ts`, `session`, `cwd`, `entrypoint`, `version`, `branch`) is done here for every line; then each consumer's `line` is called.
  - `sessions(files: dict) -> dict`: session id to `{"id", "cwd", "branch", "first_ts", "last_ts", "entrypoint", "version", "subagent_files", "files"}`, in the order a session first appears in `files`.
- Produces, in the core: `Component.lines: Consumer | None = None`. `Ctx.cache -> dict` (the pass cache, `cache.json`). `Ctx.transcripts() -> dict`. `Ctx.memo -> dict` (one dict for each component; it lives for one pass: it is emptied when a pass starts and when it ends, so outside a pass it is empty). `Core.run_pass` runs in rounds.

**The state of a file.** A new state is, in this key order: `path`, `offset: 0`, `size: 0`, `session: None`, `cwd: None`, `first_ts: None`, `last_ts: None`, `entrypoint: None`, `version: None`, `branch: None`, `sidechain: "/subagents/" in path`, then the keys of each consumer's `fresh()` in the order of the consumers, then `v: VERSION`.

**When a file is read again from the start** (today's rule plus one): it is gone from the cache, its size on disk is smaller than `size`, its `v` differs, or a key of a present consumer is missing from its state. As today, the `ids` entries owned by such files are forgotten first.

**When a consumer is gone:** each time a file moves forward, every key that is not one of the eleven head keys, `v`, or a key of a present consumer is removed from its state. A file that does not move forward keeps its state whole.

**Errors:** the head and each consumer's `line` run in their own `try`; `ValueError`, `TypeError` and `AttributeError` skip that part of that line, as today.

**`sessions(files)`**, for each state in order: `sid = state["session"] or Path(state["path"]).stem`. A sidechain state adds 1 to `subagent_files`. A state that is not a sidechain sets `cwd = cwd or state["cwd"]`, `entrypoint = entrypoint or state["entrypoint"]`, `branch = state["branch"] or branch`. Every state then sets `cwd = cwd or state["cwd"]`, `version = state["version"] or version`, `first_ts` to the smaller, `last_ts` to the larger, and is appended to `files`.

**Rounds in `run_pass`:** `for i in range(longest reader list): for c in components (name order, no error): run c.readers[i] if it has one`. The result keeps its shape (`readers` is the number of readers of the component, `errors` the list of all its errors). A `threading.RLock` of the core is held for the whole pass.

**The pass cache:** `ctx.cache` loads `home / "cache.json"` (an empty dict when missing or not an object) the first time a reader asks in a pass. At the end of the pass it is written back with `core.util.write_json`, only when it was loaded. Outside a pass `ctx.cache` and `ctx.transcripts()` raise `RuntimeError("only during a pass")`. `ctx.transcripts()` runs `transcripts.read(core.claude_dir / "projects", cache, [c.lines for each loaded component that has one, in name order])` once in a pass and returns the same dict to every later caller in that pass.

- [ ] **Step 1: Write the failing tests**

`TranscriptsTest` in `tests/test_core.py` (a temp folder as `projects_dir`; a helper `write(name, rows, mode="w")` that writes JSON lines; a counting consumer `Consumer(fresh=lambda: {"n": 0, "seen": []}, line=count)` where `count` does `st["n"] += 1` and appends the line's `type`). One test for each sentence:

- a new file gets the state keys in the order given above, with `v == "0.2.2"`;
- two passes hand each line one time (`n` equals the number of lines; a second `read` with nothing new changes nothing; appended lines are handed once);
- a last line without a newline is not handed until it is whole;
- the head facts: `session`, `cwd` and `entrypoint` keep the first value seen, `version` and `branch` the last, `first_ts` and `last_ts` the first and last timestamp;
- a line that is not an object (`"x"`) is skipped and the next line is still handed; a consumer whose `line` raises `TypeError` does not stop a second consumer;
- a file that got shorter is read again from the start (`n` is the new line count) and the `ids` it owned are gone (the consumer writes `ids[line["k"]] = owner`);
- a state with another `v` is read again;
- a deleted file leaves `files`;
- **a consumer added later:** read with no consumer, then read with one: `n` equals all lines of every file;
- **a consumer that is gone:** read with one consumer; append a line to file A only; read with no consumer: A's state has no `n`, B's state still has it; read with the consumer again: A is read from the start (`n` equals all its lines) and B is unchanged;
- `sessions`: build `files` by hand with three states (a main file `s.jsonl` with session `s`, branch `main`; a later main file of `s` with branch `dev` and no cwd; a sidechain file of `s` with another cwd and an earlier `first_ts`; and a file with no session named `lonely.jsonl`) and assert the merged values and the order `["s", "lonely"]`.

`CoreTest` additions:

```python
    ROUNDS = ("from core.component import Component\nfrom pathlib import Path\n"
              "def log(ctx, what):\n    with open(Path(ctx.home) / 'order.txt', 'a') as f:\n        f.write(what + ' ')\n"
              "def one(ctx):\n    log(ctx, '{n}1')\n    {boom}\n"
              "def two(ctx):\n    log(ctx, '{n}2')\n"
              "COMPONENT = Component(name='{n}', readers=[one, two])\n")

    def test_a_pass_runs_in_rounds(self):
        put(self.comps, "alpha", self.ROUNDS.format(n="alpha", boom="raise ValueError('x')"))
        put(self.comps, "beta", self.ROUNDS.format(n="beta", boom="pass"))
        res = Core(self.home, self.home / ".claude", self.comps).run_pass({})
        self.assertEqual((self.home / "order.txt").read_text().split(), ["alpha1", "beta1", "alpha2", "beta2"])
        self.assertEqual(res["components"]["alpha"], {"readers": 2, "errors": ["ValueError: x"]})
        self.assertEqual(res["components"]["beta"], {"readers": 2, "errors": []})
```

Further `CoreTest` tests, each with temp components: the pass cache is loaded one time and written at the end (`ctx.cache["k"] = 1` in a reader, then `cache.json` holds it; a second component's reader sees the same dict); a pass in which no reader touches the cache writes no `cache.json`; `ctx.cache` and `ctx.transcripts()` outside a pass raise `RuntimeError`; two components with `lines` both get every line of one transcript and `ctx.transcripts()` called by both readers returns the same object; `ctx.memo` is not shared between components, is seen by an offer of the same component that is asked during the pass, and is empty after the pass and in the next one.

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -m unittest tests.test_core -k TranscriptsTest -k CoreTest 2>&1 | tail -15`
Expected: FAIL (`core.transcripts` does not exist).

- [ ] **Step 3: Write `core/transcripts.py`** from `parse_transcripts`, `new_file_state`, `_owner` and the head of `_line` in `collector/collect.py`, with the rules above. Keep the order of work: find the files, sort by `(mtime, path, size)`, find the stale ones, forget their `ids`, read each file from its offset to the last newline, drop the files that are gone.

- [ ] **Step 4: Implement rounds, the pass cache, `ctx.transcripts()`, `ctx.memo` and `Component.lines`** in `core/app.py` and `core/component.py`.

- [ ] **Step 5: Make `collect.py` use the helper.** `new_file_state` and the head of `_line` are deleted. `_fresh()` returns the thirteen usage keys in today's order (`models`, `hourly`, `baseline`, `calls`, `mcp_tools`, `agent_tokens`, `context`, `recent`, `pending`, `turns`, `prompts`, `last_turn`, `cache_losses`). `_line(st, d, ids, owner)` keeps only the part after the head; it still reads `ts = d.get("timestamp")` for itself. `USAGE = transcripts.Consumer(fresh=_fresh, line=_line)`. `parse_transcripts(cache)` becomes `return transcripts.read(CLAUDE_DIR / "projects", cache, [USAGE])`. `collect()` still loads and writes `cache.json` itself (3B moves that into the pass). `VERSION` in `collect.py` stays (it is the collector's version in the snapshot); the state version is now `transcripts.VERSION`.

- [ ] **Step 6: Run the reference test, then the whole suite**

Run: `python3 -m unittest tests.test_same_output tests.test_dedupe 2>&1 | tail -4`
Expected: `OK`. The cache section of the reference test proves the state keeps its format.

Run: `python3 -X dev -m unittest discover -s tests 2>&1 | tail -4`
Expected: `OK`, nothing else printed.

- [ ] **Step 7: Add to `collector/components/README.md`**

"A pass runs in rounds: the first reader of every component, then the second, and so on; inside a round the order is the component name. Put a reader that needs what another component wrote in this pass in the second place." "`ctx.transcripts()` gives the state of every transcript file; the helper reads each new line one time and hands it to the `lines` consumer of every component. `core.transcripts.sessions(files)` groups the files into sessions." "`ctx.cache` is the pass cache (`cache.json`): bookkeeping only, never records. `ctx.memo` is a dict of your own that lives for one pass."

- [ ] **Step 8: Commit**

```bash
git add -A collector tests
git commit -m "Core: a pass runs in rounds, keeps the pass cache, and reads the transcripts one time for every component"
```

---

## What this plan leaves for 3B

Claude management as a component, the records of Project management in the store, `relocate` as an action, the checks 2 and 3 of the spec, and the documents. See `2026-11-11-core-step-3b-claude-management.md`.
