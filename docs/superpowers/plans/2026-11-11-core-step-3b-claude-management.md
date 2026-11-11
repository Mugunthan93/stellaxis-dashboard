# Stellaxis Core, Step 3B (Claude Management Becomes a Component) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move what `collect.py`, `prune.py` and `settings_api.py` do for skills, agents, plugins, connectors, CLAUDE.md files, usage and cost into the component `claude_management`; put the records of Project management in the store; make `relocate` an action of Project management; and make checks 2 and 3 of the spec hold.

**Architecture:** A collection is one pass of the core. In round one Claude management turns transcripts into use and cost for each session, and Project management finds the repos and the sessions. In round two Claude management reads the repos, reads the Claude setup and writes `snapshot.json`; Project management reads the costs and writes its index. `collect.py`, `prune.py` and `mcp_server.py` stay as thin ways in that reach components only through the core. Code moves with its bodies unchanged; only the names it takes its folders, clock and config from change.

**Tech Stack:** Python 3 stdlib, `unittest`, `sqlite3`.

**Spec:** `docs/superpowers/specs/2026-11-11-core-architecture-design.md` (words follow `CONTEXT.md`): "3. Claude management", "2. Project management", "Relationships", "Left to the builder", "How success is checked" (checks 2, 3, 4, 6).

**Before this plan:** `2026-11-11-core-step-3a-core-contract.md` is built. Its "Global Constraints" and its 25 rulings hold here too. Read both sections first.

## More constraints for this plan

- The proof is `tests/test_same_output.py`. It must pass after every task. A code move is right when that test passes without any change to the expected file or the harness.
- When a function moves, its body moves unchanged. Only these replacements are made: `CLAUDE_DIR` to `ctx.claude_dir`, `CLAUDE_JSON` to `ctx.claude_json`, `STX_HOME` to `ctx.home`, `HOME` to `core.util.HOME`, `now_utc()` to `core.util.now()`, `status(...)` to `ctx.status(...)`, a `print(...)` inside an action to `plan.note(...)` with the same text, and `cfg["key"]` to `cfg.get("key", <fallback>)` with the fallbacks named in the task.
- An in-process test that runs a collection gives a config with `"scan_roots": []` and patches `live` and `app_records` of the command centre, so it never walks the real home folder, never runs the real `claude`, and never reads the desktop app's real records.
- Read `DESIGN.md` before changing any page.

## Review Focus

1. A transcript folder with no `.claude` anywhere, and a session whose folder is gone: both still show in the snapshot and the index as before. The reference test covers both.
2. The store cannot be opened: `snapshot.json`, `projects.json` and every route still answer as before. Tests in Tasks 1 and 2.
3. One component folder is removed: the service starts, the other components run their readers and answer their routes. Tests in Task 5.
4. A settings save whose body holds a secret: the secret is in the settings file and its backup, and nowhere else. Test in Task 3.
5. An archive made before this step (step name `relocate`, no component name): it still restores. Test in Task 4.

## File Structure

| File | Responsibility |
|---|---|
| `collector/components/claude_management/__init__.py` | `COMPONENT`: two readers, the transcript consumer, records, offers, actions, undo, routes. |
| `collector/components/claude_management/usage.py` | A transcript line to use; sessions with tokens and cost; prices. |
| `collector/components/claude_management/setup.py` | Reads the Claude setup: items of the Mac and of each repo; the MCP probe. |
| `collector/components/claude_management/snapshot.py` | Joins use and setup into the snapshot; the history file. |
| `collector/components/claude_management/records.py` | The tables and what is read from them. |
| `collector/components/claude_management/trim.py` | The actions `archive` and `move` (from `prune.py`). |
| `collector/components/claude_management/settings.py` | Settings files: read, check, show the difference, save, undo, routes (from `settings_api.py`). |
| `collector/components/project_management/repos.py` | Finds the repos (from `discover_projects` and `worktree_of`). |
| `collector/components/project_management/records.py` | The tables of the index and of the repos; builds the index from the rows. |
| `collector/components/project_management/relocate.py` | The action `relocate` (from `prune.py`). |
| `collector/components/project_management/__init__.py` | Two readers, offers `repos` and `sessions`, the action `relocate` and its undo. |
| `collector/collect.py` | Config, status, the core, one pass, pages and the HTTP handler, the command line. |
| `collector/prune.py` | The command line: parses, prints, runs actions through the core. |
| `collector/settings_api.py` | Deleted. |
| `tests/test_claude_management.py` | New. Tests of the component. |
| `tests/test_components.py` | New. Checks 2 and 3: one component removed; no reads around the core. |

---

### Task 1: Claude management owns the snapshot

**Files:**
- Create: `collector/components/claude_management/__init__.py`, `usage.py`, `setup.py`, `snapshot.py`, `records.py`, `tests/test_claude_management.py`
- Modify: `collector/core/util.py` (gets `parse_duration`), `collector/collect.py`, `collector/prune.py` (`load_items`), `collector/components/project_management/__init__.py`, `dashboard/shell.html` (one text), `tests/test_dedupe.py`, `tests/test_hardening.py`, `tests/test_project_management.py`, `tests/test_core.py`, `tests/test_e2e.py`
- Test: `tests/test_same_output.py` (unchanged), `tests/test_claude_management.py`

**Interfaces:**
- Consumes (3A): `ctx.transcripts()`, `core.transcripts.Consumer`, `core.transcripts.sessions`, `ctx.cache`, `ctx.memo`, `ctx.status`, `ctx.claude_dir`, `ctx.claude_json`, `ctx.home`, `core.util.now`, rounds.
- Produces:
  - `core.util.parse_duration(s, default=60) -> int` (moved from `collect.py`, which keeps the name by importing it).
  - Pass inputs of a collection: `{"collector": {"version": str, "probe": bool, "reels": dict | None, "project_notes": list}}`.
  - `usage.LINES: Consumer`; `usage.sessions(files: dict, pricing: dict) -> list[dict]` (the session rows of the snapshot, newest first, each still holding `_hourly`, `_ctx`, `_losses`).
  - `setup.inventory(ctx, cwds: set) -> (items, projects, meta)`; `setup.run_probes(items, cfg, cache, status, version, force=False) -> dict`.
  - `snapshot.build(ctx, rows, n_files, items, projects, meta, probes, t0) -> dict`; `snapshot.append_history(home, snap)`; `snapshot.load_history(home, limit=2000) -> list`.
  - Tables `claude_management_sessions (id TEXT PRIMARY KEY, project TEXT, first_ts TEXT, last_ts TEXT, branch TEXT, cost REAL NOT NULL, data TEXT NOT NULL, hours TEXT NOT NULL)` and `claude_management_items (id TEXT PRIMARY KEY, pos INTEGER NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL, name TEXT NOT NULL, data TEXT NOT NULL)`.
  - Offers of `claude_management`: `sessions(ctx, hours=False) -> [{"id", "project", "first_ts", "last_ts", "branch", "cost"}]` (with `"hours": {hour: cost}` when asked); `items(ctx) -> [item]`; and, until Task 2 removes it, `pass(ctx) -> [{"sessions": rows, "folders": projects of the snapshot}]`.
  - `collect.collect(cfg=None, probe=False) -> dict`: the snapshot this pass wrote, else `{}`. `status.json` on idle: `index_error` is `None` or `"name: error; name: error"` for every component that has an error.

**What goes where** (bodies unchanged, see "More constraints"):

- `usage.py`: `BYTES_PER_TOKEN`, `tok`, `parse_ts`, `norm_mcp`, `CMD_RE`, `LISTING_LINE`, `CACHE_TTL_SECONDS`, `CACHE_LOSS_MIN_CONTEXT`, `_bump`, `_parse_listing`, `_attachment`, `_cache_loss`, `fresh` (was `_fresh`), `line` (was `_line`), `LINES = Consumer(fresh=fresh, line=line)`, `price_for`, `rebuild_extra`, `cost_of`. New: `sessions(files, pricing)` is the two blocks of `build_snapshot` named "merge transcript files into sessions" and "per-session totals", up to and including the sort by `last_ts`. It takes `id`, the folder (`cwd`), `first_ts`, `last_ts`, `entrypoint`, `version`, `branch` and `subagent_files` of each session from `core.transcripts.sessions(files)`, and merges its own keys (`models`, `hourly`, `calls`, `agent_tokens`, `mcp_tools`, `turns`, `cache_losses` from every file; `baseline`, `context`, `prompts` from files that are not sidechains) by walking each session's `files` in order. The row's `project` is `short_path(cwd)` or `"(unknown)"`.
- `setup.py`: `read_text`, `frontmatter`, `item`, `resolve_imports`, `scan_skill_dir`, `_origin`, `scan_md_dir`, `mcp_items`, `scan_plugin`, `installed_plugins(claude_dir)`, `worktree_of`, `discover(ctx, cwds, claude_json: dict) -> list[str]` (was `discover_projects`; it keeps its walk cache in `ctx.cache["walk"]`), `inventory(ctx, cwds)`, `probe_stdio(conf, timeout, version)`, `run_probes`. Fallbacks: `rescan_every` `"1h"`, `exclude_dirs` `[]`, `scan_roots` `[]`, `scan_depth` `6`, `probe_every` `"24h"`, `probe_timeout` `20`. `probe_stdio` closes the pipes of the process it started (it leaves them open today, which prints warnings).
- `snapshot.py`: `iso`, `interactive`, `build` (the rest of `build_snapshot`, from "synthesize items seen only in measured context" to the end), `append_history`, `load_history`. Fallbacks: `pricing` `{}`, `series_days` `30`, `interval` `"1m"`, `max_sessions` `400`. `collector_version`, `reels` and `project_notes` come from `ctx.inputs["collector"]`. `elapsed_ms` counts from `t0`.
- `collect.py` keeps: the docstring, `VERSION`, the folder constants, `DEFAULT_CONFIG`, `now_utc`, `iso`, `status`, `load_config`, `project_notes`, `reels_summary`, `the_core`, `_lock`, `collect`, everything under "serve", `print_summary`, `main`, and the names other files use (`read_json`, `write_json`, `short_path`, `parse_duration`). Everything else leaves the file. `write_index` is deleted.

**The readers of `claude_management`** (both return at once when `"collector"` is not in `ctx.inputs`):

```python
def usage_reader(ctx) -> None:        # round one
    ctx.memo["t0"] = time.time()
    files = ctx.transcripts()
    rows = usage.sessions(files, ctx.cfg.get("pricing") or {})
    ctx.memo.update(files=files, sessions=rows)
    records.save_sessions(ctx, rows, ctx.cfg.get("pricing") or {})      # does nothing when the store is down

def setup_reader(ctx) -> None:        # round two
    col, files, rows = ctx.inputs["collector"], ctx.memo["files"], ctx.memo["sessions"]
    items, projects, meta = setup.inventory(ctx, {st["cwd"] for st in files.values() if st.get("cwd")})
    probes = (setup.run_probes(items, ctx.cfg, ctx.cache, ctx.status, col["version"]) if col.get("probe")
              else ctx.cache.get("probes", {}))
    snap = snapshot.build(ctx, rows, len(files), items, projects, meta, probes, ctx.memo["t0"])
    snap["history"] = snapshot.load_history(ctx.home)
    write_json(ctx.home / "snapshot.json", snap)
    snapshot.append_history(ctx.home, snap)
    snap["history"] = snapshot.load_history(ctx.home)
    write_json(ctx.home / "snapshot.json", snap)
    ctx.memo["folders"] = snap["projects"]
    records.save_items(ctx, snap["items"])                               # does nothing when the store is down
```

`records.save_sessions` writes a row only when it is new or its `data`, `cost` or `hours` changed, and deletes the rows of sessions that are gone. `data` is the session row as JSON without the keys that start with `_`. `hours` is JSON of `{hour: cost}` from the row's `_hourly`, each cost `round(sum of cost_of(model, row, pricing)), 6)`. `records.save_items` does the same for items with `pos`. A component whose store is down (`StoreError`) skips the write and raises nothing.

**`collect()`:**

```python
def collect(cfg=None, probe=False) -> dict:
    with _lock:
        cfg = cfg or load_config()
        STX_HOME.mkdir(parents=True, exist_ok=True)
        started = time.time()
        status("reading transcripts")
        core = the_core()
        res = core.run_pass(cfg, {"collector": {"version": VERSION, "probe": probe, "reels": reels_summary(),
                                                "project_notes": project_notes(cfg)}})
        f = STX_HOME / "snapshot.json"
        snap = (read_json(f, {}) or {}) if f.exists() and f.stat().st_mtime >= started - 2 else {}
        status("idle", last_snapshot=snap.get("generated_at"), elapsed_ms=(snap.get("meta") or {}).get("elapsed_ms"),
               index_error=pass_error(core, res), core_error=core.error)
        return snap
```

`pass_error(core, res) -> str | None` joins with `"; "` first `f"{c.name}: {c.error}"` for each component with an error, then `f"{name}: {e}"` for each reader error in `res`, and gives `None` when there is none. The `serve` loop and `main` must not fail on an empty snapshot: the loop prints `no snapshot was written` instead of the counts.

**Project management in this task:** `readers=[_round_one, index_reader]`, where `_round_one` does nothing yet (Task 2 fills it). `index_reader` first calls `ctx.status("indexing projects")`, then takes its sessions and folders from `ctx.inputs` when the key `sessions` is there (the old way, kept until Task 2 so its tests stay), else, in a collection, from `ctx.read("claude_management", "pass")` (an empty answer means no sessions and no folders). `reads=[("claude_management", "pass")]`.

- [ ] **Step 1: Write the failing tests in `tests/test_claude_management.py`**

Build a small fake home in `setUp` (temp folder; `fake_home` from `tests/test_hardening.py`; one user skill `foo`; one transcript in `<claude>/projects/p/s1.jsonl` with one assistant turn of model `claude-opus-5-5` that calls the skill `foo`, cwd `<tmp>/code/app`, which exists and holds a `CLAUDE.md`; config `{"scan_roots": [], "claude_bin": "/no/claude", "pricing": {"claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.2}}}`). Patch `live` (returns `[]`) and `app_records` (returns `{}`) of the command centre. `CM = import_folder(COMPS / "claude_management")`.

- `test_the_component_loads`: in a `Core` on the real components folder, `claude_management` has no error, two readers, `lines` set, and the offers `items`, `pass`, `sessions`.
- `test_a_collection_writes_the_snapshot_and_the_rows`: `C.collect(cfg)` returns a snapshot with `skill:user:foo` in `items` (usage calls 1) and the session `s1` with cost above 0; the store has one row in each table; `core.read("claude_management", "sessions", cfg)` gives one row with the same cost and no `hours` key; with `hours=True` the row has one hour whose cost equals the session's cost within 1e-6; `core.read("claude_management", "items", cfg)` holds the skill.
- `test_a_second_collection_with_nothing_new_rewrites_no_session_row`: run two collections; between them read `SELECT total_changes()`-free proof: patch `records` to count `INSERT` calls, or compare `rowid` and a `data_version`. Simplest: after the first collection set the row's `project` column to `"marker"` by hand; after the second collection it is still `"marker"`.
- `test_a_session_that_is_gone_leaves_the_store`: delete the transcript, collect, the sessions table is empty.
- `test_with_the_store_down_the_snapshot_is_still_written`: make `<stx>/stellaxis.db` a folder before the first `the_core()`; `C.collect(cfg)` returns the snapshot with the item; `status.json` has `core_error` set and `index_error` `None`.
- `test_a_pass_that_is_not_a_collection_reads_nothing`: `core.run_pass(cfg)` writes no `snapshot.json` and no `cache.json`.
- `test_a_component_with_an_empty_config_does_the_least`: `C.collect({})` after patching `live`: no error in `status.json`, a snapshot with the item, cost 0.
- `test_every_components_error_is_in_the_status`: patch `build_index` of the command centre to raise `ValueError("boom")` and `CM.snapshot.build` to raise `KeyError("k")`; after `C.collect(cfg)`, `index_error` is `"claude_management: KeyError: 'k'; project_management: ValueError: boom"`.

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -m unittest tests.test_claude_management 2>&1 | tail -6`
Expected: FAIL (no such component).

- [ ] **Step 3: Create the component and thin `collect.py`** as described above. Declare `COMPONENT = Component(name="claude_management", readers=[usage_reader, setup_reader], lines=usage.LINES, records=records.RECORDS, offers={"sessions": ..., "items": ..., "pass": ...})`. The `pass` offer returns `[{"sessions": ctx.memo["sessions"], "folders": ctx.memo["folders"]}]` when both are in the memo, else `[]`.

- [ ] **Step 4: Run the reference test**

Run: `python3 -m unittest tests.test_same_output 2>&1 | tail -30`
Expected: `OK`. If a value differs, the test names its place; fix the code, never the expected file.

- [ ] **Step 5: Bring the other tests along**

- `tests/test_dedupe.py`: use `core.transcripts.read(root, cache, [CM.usage.LINES])`, `CM.usage._attachment` and `CM.usage.norm_mcp`.
- `tests/test_hardening.py`: `Probes` calls `CM.setup.run_probes(items, cfg, cache, lambda *a, **k: None, "t")`. `IndexFailure.test_index_error_stays_on_the_idle_status` runs a real `C.collect(cfg)` on a fake home with `build_index` patched to raise and expects `"project_management: ValueError: boom"`. `test_collection_clears_stale_switches` runs `C.collect(cfg)`. `RouteMatrix` keeps mocking `C.collect`.
- `tests/test_project_management.py`: the tests that called `C.write_index` call `self.core.run_pass(cfg, {"sessions": [...], "folders": []})` and read the errors from the result; the one about the uncapped list is dropped here (the reference test and `tests/test_e2e.py` prove it); `test_the_component_loads` expects two readers; `test_nothing_outside_the_core_imports_the_component` also checks `claude_management import`. `BrokenComponentTest`: the child calls `collect.collect({"scan_roots": []})` and prints `index_error` from `status.json`; expect `"project_management: RuntimeError: boom"`.
- `tests/test_core.py`: `WiringTest` still passes; fix what named moved functions.
- `dashboard/shell.html`: the text `"Project index failed: "` becomes `"A component failed: "`. The test in `tests/test_hardening.py` that reads this file still passes.
- `collector/prune.py`: `load_items()` exits with `no snapshot: Claude management wrote none` (exit code 1) when there is no snapshot after a collection.

- [ ] **Step 6: Run the whole suite**

Run: `python3 -X dev -m unittest discover -s tests 2>&1 | tail -4`
Expected: `OK`, nothing else printed.

- [ ] **Step 7: Commit**

```bash
git add -A collector tests dashboard
git commit -m "Claude management: a component that reads the transcripts and the Claude setup and writes the snapshot; collect.py runs one pass"
```

---

### Task 2: Project management stands alone and keeps its records in the store

**Files:**
- Create: `collector/components/project_management/repos.py`, `collector/components/project_management/records.py`
- Modify: `collector/components/project_management/__init__.py`, `collector/components/claude_management/__init__.py`, `collector/components/claude_management/setup.py`, `tests/test_project_management.py`, `tests/test_hardening.py`, `tests/test_claude_management.py`, `tests/test_old_names.py` (only where it fed the reader by inputs)
- Test: `tests/test_project_management.py`, `tests/test_same_output.py`

**Interfaces:**
- Produces:
  - `repos.worktree_of(P: Path) -> str | None` and `repos.discover(ctx, cwds: set) -> list[dict]`: `[{"path": short path, "abs": str(P), "raw": the string as found, "has_git": bool, "worktree_of": str | None}]`, sorted by `raw`. It is `discover_projects` plus the three facts `inventory` computed for each folder (`has_git` is `True` for a worktree, else `(P / ".git").exists()`). It reads the keys of `projects` in `ctx.claude_json` itself. Fallbacks as in Task 1.
  - Offers of `project_management`: `repos(ctx) -> list[dict]` (the rows above: this pass's when a pass is running, else the store's); `sessions(ctx) -> [{"id", "project", "repo", "branch", "first_ts", "last_ts"}]` from the store (`project` is the project id, `repo` the repo path of the index).
  - `claude_management` reads `("project_management", "repos")`. `project_management` reads `("claude_management", "sessions")`. The offer `pass` and `setup.discover` and `setup.worktree_of` are deleted.
  - `records.RECORDS`, `records.save_repos(ctx, rows)`, `records.repos(ctx) -> list`, `records.save_index(ctx, index, seq: dict)`, `records.index(ctx, costs: dict) -> dict | None` (`None` when the store is down or holds no index), `records.sessions(ctx) -> list`.

**Tables** (each is written whole, in one transaction, in every collection):

```sql
CREATE TABLE IF NOT EXISTS project_management_repos (pos INTEGER PRIMARY KEY, path TEXT NOT NULL, abs TEXT NOT NULL, raw TEXT NOT NULL, has_git INTEGER NOT NULL, worktree_of TEXT)
CREATE TABLE IF NOT EXISTS project_management_runs (id INTEGER PRIMARY KEY CHECK (id = 1), generated_at TEXT NOT NULL)
CREATE TABLE IF NOT EXISTS project_management_projects (pos INTEGER PRIMARY KEY, id TEXT NOT NULL, name TEXT NOT NULL)
CREATE TABLE IF NOT EXISTS project_management_index_repos (project TEXT NOT NULL, pos INTEGER NOT NULL, path TEXT NOT NULL, abs TEXT NOT NULL, here INTEGER NOT NULL, state TEXT NOT NULL, PRIMARY KEY (project, pos))
CREATE TABLE IF NOT EXISTS project_management_sessions (id TEXT PRIMARY KEY, project TEXT NOT NULL, repo INTEGER NOT NULL, branch TEXT NOT NULL, branch_pos INTEGER NOT NULL, pos INTEGER NOT NULL, seq INTEGER NOT NULL, title TEXT NOT NULL, first_ts TEXT, last_ts TEXT, app_id TEXT, archived INTEGER NOT NULL, path TEXT NOT NULL)
```

`here` is the `exists` of the index (`exists` is an SQL word). `state` is JSON. `seq` is the place of the session in the list the index was built from. Cost is not stored: it belongs to Claude management.

**`records.index(ctx, costs)`** builds, from the rows alone, the same dict that `centre.build_index` returns, key for key and in the same order: `{"generated_at", "projects": [{"id", "name", "cost_30d", "repos": [{"path", "abs", "exists", "state", "branches": [{"name", "sessions": [{"id", "title", "first_ts", "last_ts", "cost", "app_id", "archived", "path"}]}]}]}]}`. A session's `cost` is `costs.get(id) or 0`. A project's `cost_30d` is the sum, in `seq` order, of the cost of its sessions whose `(last_ts or "")[:19]` is not before the cut, rounded to 2; the cut is `generated_at` minus 30 days, formatted `%Y-%m-%dT%H:%M:%S`.

**The readers** (both return at once when `"collector"` is not in `ctx.inputs`; both work inside `with centre.at(ctx):`):

```python
def repos_reader(ctx) -> None:        # round one
    files = ctx.transcripts()
    found = repos.discover(ctx, {st["cwd"] for st in files.values() if st.get("cwd")})
    ctx.memo.update(repos=found, sessions=transcripts.sessions(files))
    records.save_repos(ctx, found)                                       # does nothing when the store is down

def index_reader(ctx) -> None:        # round two
    ctx.status("indexing projects")
    cfg = old_names.config(ctx.cfg)
    centre.clear_stale(centre.live(centre._bin(cfg)))
    costs = {r["id"]: r["cost"] for r in ctx.read("claude_management", "sessions")}
    rows = sorted(({"id": s["id"], "project": short_path(s["cwd"]) if s["cwd"] else "(unknown)", "first_ts": s["first_ts"],
                    "last_ts": s["last_ts"], "cost": costs.get(s["id"]) or 0, "branch": s["branch"]}
                   for s in ctx.memo["sessions"].values()), key=lambda r: r["last_ts"] or "", reverse=True)
    index = centre.build_index(rows, ctx.memo["repos"], centre.app_records(centre.app_base()), cfg)
    records.save_index(ctx, index, {r["id"]: i for i, r in enumerate(rows)})
    write_json(ctx.home / centre.INDEX_FILE, records.index(ctx, costs) or index)
    write_json(ctx.home / old_names.INDEX_FILE, old_names.answer(index))
```

The offer `sessions` of Claude management answers from this pass's rows while a pass is running (`ctx.memo`), else from the store; so does the offer `repos` here. That keeps both files right when the store is down. `ctx.memo` is empty outside a pass (3A).

**Claude management:** `setup.inventory(ctx, repos: list)` takes the rows of the offer in place of `cwds`. For each row: `P = Path(row["abs"])`, `sp = row["path"]`, `wt = row["worktree_of"]`; the lookup in `~/.claude.json` uses `row["raw"]`; `has_git` comes from the row. The dicts it appends keep their keys and key order (`path`, `abs`, `items`, `ancestor_claude_md`, `has_git`, and `worktree_of` only for a worktree). `setup_reader` calls `setup.inventory(ctx, ctx.read("project_management", "repos"))`.

- [ ] **Step 1: Write the failing tests in `tests/test_project_management.py`**

Give `ProjectManagementTest.setUp` a transcript (`<home>/.claude/projects/p/s1.jsonl`, one user line with `sessionId` `s1`, `cwd` `<home>/code/app` which exists, `gitBranch` `main`, timestamp `2026-11-01T00:00:00Z`) and a helper `collect(cfg=CFG, **more)` that runs `self.core.run_pass({**cfg, "scan_roots": []}, {"collector": {"version": "t", "probe": False, "reels": None, "project_notes": []}})` with `live` and `app_records` patched. Replace the tests that fed sessions by inputs:

- `test_a_collection_writes_the_index_from_the_transcripts`: the index has the group `unassigned` with one repo (the folder, in its short form) and the session `s1` on the branch `main`; the result has no errors for any component.
- `test_the_index_built_from_the_rows_is_the_index_built_in_memory`: call `W.build_index(sessions, folders, app, cfg, now="2026-11-11T12:00:00Z")` on a hand-made input with two projects, one of them with a repo that has no sessions (the folder exists), a worktree session, two branches in one repo, a session with `last_ts` older than 30 days, an archived session with an app title, and the group `unassigned`; `records.save_index(ctx, index, seq)`; then `json.dumps(records.index(ctx, costs))` equals `json.dumps(index)`, where `costs` holds the costs the sessions were given. `ctx` is `self.core.ctx("project_management", {})`.
- `test_the_repos_and_sessions_offers`: after a collection, `core.read("project_management", "repos", cfg)` gives one row with `path`, `abs`, `raw`, `has_git` `False`, `worktree_of` `None`; `core.read("project_management", "sessions", cfg)` gives `[{"id": "s1", "project": "unassigned", "repo": <short path>, "branch": "main", ...}]`.
- `test_with_the_store_down_the_index_is_still_written`: a `Core` on a home whose `stellaxis.db` is a folder; a collection writes `projects.json` with `s1`; no error for `project_management`.
- `test_without_claude_management_the_cost_is_zero`: patch the `sessions` offer of the loaded `claude_management` component to raise; the index is written, the session's cost is 0, and the pass has no error for `project_management`.
- `test_a_pass_that_is_not_a_collection_leaves_the_index_alone`: `self.core.run_pass(dict(CFG))` writes no `projects.json`.
- `test_the_component_loads` expects two readers and the offers `approvals`, `index`, `live`, `projects`, `repos`, `sessions`.

In `tests/test_claude_management.py`: `test_the_items_of_a_repo_come_from_the_repos_project_management_found` (the app folder's `CLAUDE.md` is an item `claude_md:project:<short path>:CLAUDE.md`), and `test_without_project_management_only_the_macs_own_items_are_listed` (patch the `repos` offer of the loaded `project_management` component to raise: the snapshot is written, holds `skill:user:foo`, holds no `claude_md:project:` item, and still lists the session and its folder).

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -m unittest tests.test_project_management tests.test_claude_management 2>&1 | tail -8`
Expected: FAIL.

- [ ] **Step 3: Implement** `repos.py`, `records.py`, the two readers and the two offers in Project management; the change in `setup.inventory` and `setup_reader`; delete `setup.discover`, `setup.worktree_of`, the `pass` offer, `ctx.memo["folders"]`, the old inputs path of `index_reader` and `_round_one`. Declare `reads` in both components.

- [ ] **Step 4: Run the reference test, then the whole suite**

Run: `python3 -m unittest tests.test_same_output 2>&1 | tail -30`
Expected: `OK`.

Run: `python3 -X dev -m unittest discover -s tests 2>&1 | tail -4`
Expected: `OK`, nothing else printed. Bring along any test that still fed the reader by inputs (`tests/test_hardening.py`, `tests/test_old_names.py`).

- [ ] **Step 5: Commit**

```bash
git add -A collector tests
git commit -m "Project management: finds the repos and the sessions itself, keeps its index in the store, and takes only the cost from Claude management"
```

---

### Task 3: Archive, move and the settings edits become actions of Claude management

**Files:**
- Create: `collector/components/claude_management/trim.py`, `collector/components/claude_management/settings.py`
- Delete: `collector/settings_api.py`
- Modify: `collector/components/claude_management/__init__.py`, `collector/components/claude_management/records.py`, `collector/prune.py`, `collector/collect.py`, `dashboard/settings.html` (one comment), `tests/test_settings_api.py`, `tests/test_hardening.py`, `tests/test_core.py`, `tests/test_claude_management.py`
- Test: `tests/test_same_output.py`, `tests/test_settings_api.py`, `tests/test_claude_management.py`

**Interfaces:**
- Consumes (3A): `run_action(..., plan=)`, `Plan.note`, `Plan.result`, `private`, undo handlers with `ctx`, `core.server.ApiError`, `read_body`, `send_json`, `host_ok`, `core.undo.dig`, `core.undo.pre`.
- Produces:
  - Actions of `claude_management`: `archive(plan, ctx, ids: list) -> True`, `move(plan, ctx, ids: list, to: str) -> True`, `save_settings(plan, ctx, body: dict) -> True` with `save_settings.private = ("body",)`.
  - Undo step `claude_management.settings_file`: `{"op", "file", "backup"}` (`backup` is `None` when the file did not exist).
  - `records.items_by_id(ctx) -> dict`: the items from the store; when the store is down or empty, from `ctx.home / "snapshot.json"`.
  - The routes `GET /api/settings`, `POST /api/settings/preview`, `POST /api/settings/save` are answered by the component's `handle`.

**`trim.py`:** `expand`, `mcp_location(ctx, it)`, `edit_json(plan, path, fn, desc)`, `archive_item(plan, ctx, it)`, `move_item(plan, ctx, it, to)` move from `prune.py`. Every `print` becomes `plan.note` with the same text. The two actions loop over `ids`; an id that is not known gives ``plan.note(f"  unknown id {iid} (run `prune.py list`)")``.

**`prune.py`, for `archive` and `move`:** as today it first calls `load_items()` (so a snapshot exists), builds its own `Plan(a.apply, a.cmd)` and prints the line `Plan: …` or `Applying: …`. Then `C.the_core().run_action("claude_management", a.cmd, {"ids": a.ids} or {"ids": a.ids, "to": a.to}, a.apply, C.load_config(), plan=plan)`. The core finishes the plan, so `prune.py` does not call `plan.finish()` for these two. When the component is not loaded (`KeyError`), it exits with `Claude management is not loaded` (exit code 1).

**`settings.py`:** everything in `settings_api.py` between the docstring and the HTTP section moves, with a `ctx` as first argument where a folder is needed (`known_projects(ctx)`, `settings_path(ctx, scope, project)`, `load`, `_plan`, `preview`, `save`). `known_projects` reads `ctx.home / "snapshot.json"`; the repo of Stellaxis itself is `Path(__file__).resolve().parents[3]`.

```python
def save_settings(plan, ctx, body: dict) -> bool:
    if not plan.apply:
        plan.result = preview(ctx, body)
        plan.say(f"save {short_path(plan.result['path'])}")
        return True
    plan.result = save(ctx, body)
    plan.say(f"save {short_path(plan.result['path'])}")
    plan.manifest["entries"].append({"op": "claude_management.settings_file", "file": plan.result["path"], "backup": plan.result["backup"]})
    return True
save_settings.private = ("body",)
```

The undo handler `settings_file(entry, apply, out, ctx)`: with a backup, it says `put <file> back from <backup>` and, when applied, writes the backup's bytes over the file with `atomic_write`; when the backup is missing it says so and returns `False`. With no backup it says `remove <file>: it did not exist before the save` and, when applied, removes the file if it is there. Each line starts with `core.undo.pre(apply)`.

`handle(handler, method, ctx)` is `settings_api.handle` with the same paths, checks, statuses and texts; `preview` answers `ctx.act("save_settings", {"body": body}, False)["result"]` and `save` answers `ctx.act("save_settings", {"body": body}, True)["result"]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_settings_api.py`: keep every test. Change `setUpClass` to serve through the core: `fake_home(cls, T / "stx", cls.cfg)`, `cls.core = Core(T / "stx", cls.cfg, COMPS)`, `cls.srv = core.server.serve(cls.core, 0, {})`. Where a test used `settings_api.CLAUDE_DIR` or `settings_api.host_ok`, use `cls.cfg` and a request with a foreign `Host`. Restore the two environment variables in `tearDownClass` (`fake_home` does it).

`tests/test_claude_management.py`, class `SettingsActions` (same fake home as Task 1, a `settings.json` with `{"model": "opus"}`):

```python
    def test_a_save_can_be_undone_and_its_data_is_not_logged(self):
        f = self.claude / "settings.json"
        before = f.read_bytes()
        body = {"scope": "user", "data": {"model": "sonnet", "env": {"TOKEN": "s3cret-value"}}, "base_sha": hashlib.sha256(before).hexdigest()}
        r = self.core.run_action("claude_management", "save_settings", {"body": body}, True, self.cfg)
        self.assertEqual(json.loads(f.read_text())["model"], "sonnet")
        self.assertTrue(Path(r["result"]["backup"]).is_file())
        self.assertNotIn("s3cret-value", (self.stx / "actions.jsonl").read_text())
        self.assertNotIn("s3cret-value", Path(r["manifest"]).read_text())
        undo.restore(Path(r["manifest"]).parent.name, True, self.claude / "_archive", handlers=self.core.undo_handlers(self.cfg), out=lambda _: None)
        self.assertEqual(f.read_bytes(), before)

    def test_undoing_the_save_of_a_new_file_removes_it(self):
        ...   # scope "local" in a known project folder, base_sha None: the file is made, restore removes it

    def test_a_preview_writes_nothing_and_logs_nothing(self):
        ...   # apply False: result has "diff", settings.json is unchanged, no actions.jsonl, no _archive folder

    def test_a_refused_save_changes_nothing(self):
        ...   # wrong base_sha: ApiError 409 comes out of run_action, the file is unchanged, the log row has ok False
```

Class `TrimActions`: `archive` as a dry run through `self.core.run_action("claude_management", "archive", {"ids": ["skill:user:foo", "nope:x:y"]}, False, self.cfg)` gives `steps` with one `move …` line, `notes` with the unknown id line, and changes nothing; applied, the skill folder is gone and `prune`'s `restore` (through `undo.restore` with the core's handlers) brings it back; with the store down the action still finds its items (from `snapshot.json`).

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -m unittest tests.test_claude_management tests.test_settings_api 2>&1 | tail -8`
Expected: FAIL.

- [ ] **Step 3: Implement** the two files, the actions, the undo step and the routes in the component; thin `prune.py`; delete `collector/settings_api.py`; in `collect.py` remove `import settings_api` and both `settings_api.handle` calls (a `POST` nobody answers is still a 404). In `dashboard/settings.html` the comment that names `collector/settings_api.py` names `collector/components/claude_management/settings.py`.

- [ ] **Step 4: Bring the other tests along**

`tests/test_hardening.py`: `RouteMatrix` no longer patches `C.settings_api`; it gives the two folders through `fake_home`, and its `/api/settings` rows keep their statuses. `tests/test_core.py`: `test_the_http_helpers_have_one_home` is deleted.

- [ ] **Step 5: Run the reference test, then the whole suite**

Run: `python3 -m unittest tests.test_same_output 2>&1 | tail -30`
Expected: `OK` (every prune command prints the same; every settings route answers the same; the settings files after the saves are the same).

Run: `python3 -X dev -m unittest discover -s tests 2>&1 | tail -4`
Expected: `OK`, nothing else printed.

- [ ] **Step 6: Commit**

```bash
git add -A collector tests dashboard
git commit -m "Claude management: archive, move and the settings save are actions, run and undone through the core; settings_api.py is gone"
```

---

### Task 4: Relocate becomes an action of Project management

**Files:**
- Create: `collector/components/project_management/relocate.py`
- Modify: `collector/components/project_management/__init__.py`, `collector/prune.py`, `collector/core/app.py`, `tests/test_relocate.py`, `tests/test_core.py`, `tests/test_project_management.py`
- Test: `tests/test_same_output.py`, `tests/test_relocate.py`

**Interfaces:**
- Produces: the action `relocate(plan, ctx, src: str, dest: str, force: bool = False, backups: bool = True) -> bool` of `project_management`; the undo step `project_management.relocate` (`{"op", "from", "to"}`); `relocate.claude_dir_name(path: str) -> str`.
- Core: when an action returns `False` in a dry run, the core does not finish the plan (so no "Nothing changed" line follows a refusal). In every other case it finishes it as today.

**`relocate.py`:** everything in `prune.py` under "relocate a project folder" moves (`WALK_SKIP`, `CWD_RE`, `claude_dir_name`, `remap`, `json_str`, `replace_path_field`, `rewrite_file`, `transcript_cwd`, `pid_alive`, `git_worktrees`, `repos_touching`, `desktop_files`, `desktop_running`, `remap_tree`, `rewrite_json_paths`, `reference_files(ctx)`, `relocate`). The desktop folder is `core.util.HOME / "Library" / "Application Support" / "Claude"`, computed when called. The repo of Stellaxis itself is `Path(__file__).resolve().parents[3]`. Every `print` becomes `plan.note` with the same text. The manifest entry is `{"op": "project_management.relocate", "from": s, "to": d}`.

The undo handler: `relocate(Plan(apply, "undo", ctx.claude_dir / "_archive", out=out), ctx, entry["to"], entry["from"], backups=False)`. That inner plan is never finished, as today.

**`prune.py`:** for `relocate` it prints its line, then `ok = core.run_action("project_management", "relocate", {"src": str(src), "dest": str(dest), "force": a.force}, a.apply, cfg, plan=Plan(a.apply, "relocate"))["ok"]` and exits with 1 when `ok` is false. `restore` adds one alias: when the core's handlers hold `project_management.relocate`, the same handler is also given under the old step name `relocate`. What stays in `prune.py`: the docstring, `ARCHIVE`, `load_items`, `Plan`, `restore`, `cmd_list`, `main`.

- [ ] **Step 1: Write the failing tests**

`tests/test_core.py`, `ActionTest`:

```python
    def test_a_refused_dry_run_is_not_finished(self):
        said = []
        plan = undo.Plan(False, "mine", self.home / ".claude" / "_archive", out=said.append)
        r = self.core.run_action("alpha", "refuse", {}, False, {}, plan=plan)
        self.assertEqual((r["ok"], said), (False, ["  [dry-run] refused"]))
```

`tests/test_relocate.py`: take `claude_dir_name` from `import_folder(REPO / "collector" / "components" / "project_management").relocate`. Add:

```python
    def test_an_archive_from_before_this_step_still_restores(self):
        self.prune("relocate", str(self.app), str(self.dest), "--apply")
        man = next((self.C / "_archive").glob("*-relocate/manifest.json"))
        data = json.loads(man.read_text())
        for e in data["entries"]:
            if e["op"] == "project_management.relocate":
                e["op"] = "relocate"
        man.write_text(json.dumps(data))
        self.prune("restore", "last", "--apply")
        self.assertTrue(self.app.is_dir() and not self.dest.exists())
```

(Use the names the file already has for its helper that runs `prune.py` and for the destination folder.) And a test that the manifest of a new relocation holds the step `project_management.relocate`.

`tests/test_project_management.py`: `test_the_component_loads` expects the actions `new_names` and `relocate`.

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -m unittest tests.test_core tests.test_relocate tests.test_project_management 2>&1 | tail -8`
Expected: FAIL.

- [ ] **Step 3: Implement** the file, the action, the undo step, the core's rule for a refused dry run, and thin `prune.py`.

- [ ] **Step 4: Run the reference test, then the whole suite**

Run: `python3 -m unittest tests.test_same_output 2>&1 | tail -30`
Expected: `OK` (the three `relocate` commands, the refusal with exit code 1, and the three restores print the same).

Run: `python3 -X dev -m unittest discover -s tests 2>&1 | tail -4`
Expected: `OK`, nothing else printed.

- [ ] **Step 5: Commit**

```bash
git add -A collector tests
git commit -m "Project management: relocate is an action with the same dry run, manifest and undo; prune.py is a command line only"
```

---

### Task 5: The checks, and the documents

**Files:**
- Create: `tests/test_components.py`
- Modify: `collector/collect.py` (only if a check finds a gap), `README.md`, `CLAUDE.md`, `collector/components/README.md`, `CONTEXT.md`, `tests/test_project_management.py` (move `IndependenceTest` into `tests/test_components.py`)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the tests of check 2 in `tests/test_components.py`**

One class `RemoveOneTest` with a helper `child(removed: str) -> dict`. It copies `collector/` and `dashboard/` into a temp folder (ignoring `__pycache__`), deletes `collector/components/<removed>`, makes a fake home (`<home>/.claude/skills/foo/SKILL.md`; a transcript `<home>/.claude/projects/p/s1.jsonl` with one assistant turn of model `claude-opus-5-5`, `sessionId` `s1`, `cwd` `<home>/code/app`, which exists and holds a `CLAUDE.md`; `<home>/.stellaxis/config.json` with `{"scan_roots": [], "claude_bin": "/usr/bin/false", "port": 1}`), and runs a child Python in the copied `collector/` with `HOME`, `STELLAXIS_HOME` and no `CLAUDE_CONFIG_DIR`. The child imports `collect`, `prune` and `mcp_server`, calls `collect.collect()`, serves `collect.Handler` on port 0, and prints one JSON object: the names and errors of the components from `GET /api/core`; the status codes of `/overview`, `/api/projects`, `/api/settings`, `/api/memory` and `/api/status`; the body of `/api/status`; whether `snapshot.json` and `projects.json` exist; and the item ids and session costs found in whichever of the two files exists. The test asserts the child's exit code is 0 and its stderr is empty.

```python
    def test_without_claude_management(self):
        out = self.child("claude_management")
        self.assertEqual(out["components"], {"memory_management": None, "project_management": None})
        self.assertEqual((out["status"]["phase"], out["status"]["index_error"]), ("idle", None))
        self.assertEqual((out["codes"]["/overview"], out["codes"]["/api/projects"], out["codes"]["/api/memory"]), (200, 200, 200))
        self.assertEqual(out["codes"]["/api/settings"], 404)
        self.assertEqual((out["snapshot"], out["index_sessions"]), (False, {"s1": 0}))

    def test_without_project_management(self):
        out = self.child("project_management")
        self.assertEqual(out["components"], {"claude_management": None, "memory_management": None})
        self.assertEqual((out["status"]["phase"], out["status"]["index_error"]), ("idle", None))
        self.assertEqual((out["codes"]["/overview"], out["codes"]["/api/settings"], out["codes"]["/api/memory"]), (200, 200, 200))
        self.assertEqual(out["codes"]["/api/projects"], 404)
        self.assertIn("skill:user:foo", out["items"])
        self.assertGreater(out["snapshot_sessions"]["s1"], 0)
        self.assertFalse(out["index"])

    def test_without_memory_management(self):
        out = self.child("memory_management")
        self.assertEqual(out["components"], {"claude_management": None, "project_management": None})
        self.assertEqual(out["status"]["index_error"], None)
        self.assertEqual((out["codes"]["/api/projects"], out["codes"]["/api/settings"], out["codes"]["/api/memory"]), (200, 200, 404))
        self.assertTrue(out["snapshot"] and out["index"])
        self.assertGreater(out["index_sessions"]["s1"], 0)
```

Also `test_with_no_component_at_all` (delete all three folders): the three scripts import, the collection does not raise, `/api/core` lists nothing, `/overview` answers 200. And `test_prune_says_so_when_claude_management_is_gone`: in that same copy, `prune.py list` and `prune.py archive skill:user:foo` each exit with code 1, print one plain sentence and no traceback.

Move `IndependenceTest` (check 3) from `tests/test_project_management.py` into this file, unchanged.

- [ ] **Step 2: Run them**

Run: `python3 -m unittest tests.test_components 2>&1 | tail -12`
Expected: they pass if Tasks 1 to 4 left no gap. If one fails, the gap is in `collect.py`, `prune.py` or a component: fix it there, with the least code.

- [ ] **Step 3: Write the documents**

- `README.md`: rewrite. Keep "Install", "What it measures", "Trimming" and "Decisions" as they are in substance. Replace the opening and the drawing by the architecture: one core, components with readers, records and actions, the store, the ways in. Replace "Layout" by the present files (`collector/core/`, each component folder with one line for each file, `collector/collect.py`, `prune.py`, `mcp_server.py`, `unplaced/`, the pages). Say which files are in no component yet. Use the words of `CONTEXT.md`. Short sentences.
- `CLAUDE.md`: rewrite the section "Architecture" for the present layout: what `collect.py` still holds; the pass in rounds; the transcript helper and `cache.json`; each component with its files, readers, records, offers, actions and routes; `prune.py` as a command line; the new core names (`Ctx` fields, `changes`, `lines`, `plan=`, `private`). Update "Tests" (the reference test and how to rebuild its expected file; `tests/test_components.py`). Remove every sentence that names `settings_api.py`, `write_index`, `parse_transcripts`, `inventory` or `build_snapshot` as living in `collect.py`.
- `collector/components/README.md`: one tidy list of the rules a component follows (gathered from the lines added in 3A and here), then one line for each component: what it reads from which other component.
- `CONTEXT.md`: no new word is needed. Check that the two relationship lines about cost, repos and "installed things" say what the code now does; change a line only if it is wrong.

- [ ] **Step 4: Run the whole suite**

Run: `python3 -X dev -m unittest discover -s tests 2>&1 | tail -4`
Expected: `OK`, nothing else printed.

- [ ] **Step 5: Commit**

```bash
git add -A collector tests README.md CLAUDE.md CONTEXT.md
git commit -m "Checks 2 and 3: one component removed and the rest still runs; documents describe the core and its components"
```

---

## What this plan leaves for later plans

- **Step 4:** the weekly share of the Claude plan for each project (warn only).
- **Removing the alias layer** (see the 2B plan).
- **Memory management:** the next step of its own design; it may use `ctx.service` in place of its first-pass rule.
