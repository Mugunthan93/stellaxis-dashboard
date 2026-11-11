# Memory Management Read Side Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new component, `memory_management`, that measures folder sizes in the background, keeps the last measurement in the store, and answers `GET /api/memory`. It never deletes, moves or changes a file.

**Architecture:** Three files in `collector/components/memory_management/`. `walk.py` is the pure walker: places in, rows out, no store and no threads. `records.py` writes and reads the three tables. `__init__.py` holds the config, the one background thread per Stellaxis home, the reader the core calls every pass, and the route. The reader only looks at the clock and returns.

**Tech Stack:** Python 3 standard library only (`os`, `threading`, `sqlite3`, `fnmatch`, `json`). The code must run on Python 3.9 and 3.14: start every file with `from __future__ import annotations`, no `match`, no runtime `X | Y` types.

**Spec:** `docs/superpowers/specs/2026-11-11-memory-management-design.md` (read it first). Main spec: `docs/superpowers/specs/2026-11-11-core-architecture-design.md`. Words: `CONTEXT.md`.

## Global Constraints

- New files only, plus one new bullet in `CLAUDE.md`. Do not edit `collector/core/`, `collector/collect.py`, `collector/components/project_management/`, any page, or any existing test file.
- A component imports only the standard library, `core.*` and its own files. Never `collect`, `workspaces`, `unplaced`, or another component.
- Tables are named `memory_management_…`. Each statement in `RECORDS` must start with `CREATE TABLE IF NOT EXISTS memory_management_` (the store refuses anything else).
- A connection from `ctx.db()` is the caller's to close: `with closing(ctx.db()) as db, db:`.
- No code path deletes, moves, renames, chmods or writes any file except the store through `ctx.db()`.
- The walker never follows a symlink, never enters another volume, never opens a guarded folder.
- Nothing prints. No output on stdout or stderr from the component or its tests (the suite's output must stay clean).
- Tests never touch the real `~/.stellaxis`, `~/.claude` or the real disk outside temp folders. Get the component module only with `core.component.import_folder(COMPS / "memory_management")`. Never create a temp component folder named `memory_management` or `project_management`.
- Never run `launchctl`, `collect.py serve`, `git stash`, `git push`, or anything with `sudo`. Commit only on the current branch.
- Each commit message ends with a blank line and `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`.
- Run tests with `python3 -m unittest tests.test_memory_management -v` (this file) and `python3 -m unittest discover -s tests` (all, about 45 s, 224 tests before this plan).

## Review Focus

1. **A folder vanishes during the walk** (`node_modules` is being reinstalled). Expected: the walk goes on, no exception, the vanished folder is not listed as "not measured". Test in Task 1.
2. **The Stellaxis home is deleted while a measurement runs** (other test files delete their temp home). Expected: the thread ends without output and creates no file in the deleted folder. Test in Task 3.
3. **Bad config** (`"memory": 5`, a place with no path, `"every": "soon"`, `"depth": "x"`). Expected: the bad part falls back to its default or is dropped; the reader never raises. Test in Task 3.
4. **A very deep tree** (1,200 levels). Expected: no `RecursionError`; the walker uses its own stack. Test in Task 1.
5. **A place that reaches a guarded folder by another spelling** (`~/documents`, or a symlink that points at a guarded folder). Expected: refused with "guarded by macOS", nothing listed. Test in Task 1.

Not tested, by design: "another volume" (a test cannot mount a disk). The reviewer checks that code by reading.

---

### Task 1: The walker

**Files:**
- Create: `collector/components/memory_management/walk.py`
- Create: `collector/components/memory_management/__init__.py` (for now only: `from __future__ import annotations`, `from core.component import Component`, `from . import walk`, `COMPONENT = Component(name="memory_management")`)
- Test: `tests/test_memory_management.py` (new)

**Interfaces:**
- Produces, in `walk.py`:

```python
GUARDED_HOME = ["Desktop", "Documents", "Downloads", "Pictures", "Movies", "Music", ".Trash",
                "Library/Mail", "Library/Messages", "Library/Safari", "Library/Calendars", "Library/Reminders",
                "Library/Photos", "Library/Containers", "Library/Group Containers", "Library/Daemon Containers",
                "Library/Mobile Documents", "Library/CloudStorage", "Library/Application Support/AddressBook",
                "Library/Application Support/CloudDocs", "Library/Application Support/MobileSync",
                "Library/Application Support/FileProvider", "Library/Caches/com.apple.*", "Library/Caches/CloudKit",
                "Library/Caches/FamilyCircle"]          # under the home folder
GUARDED_ABS = ["/Volumes", "/Network"]
DEPS = {"node_modules", "Pods", "bower_components"}
BUILD = {"dist", "build", ".next", ".nuxt", ".output", "out", "target", ".turbo", ".parcel-cache", ".svelte-kit",
         ".expo", ".angular", "DerivedData", ".gradle", "coverage"}
MAX_SKIPPED = 200
scandir = os.scandir            # the walker calls walk.scandir(path); tests replace it

class Guard:
    def __init__(self, paths: list[str]): ...      # absolute paths; the last part may hold * or ?
    def hit(self, path: str) -> bool: ...          # this folder itself is guarded
    def under(self, path: str) -> bool: ...        # this folder or any folder above it is guarded

def measure(places: list[dict], guard: Guard, deadline: float, progress: dict) -> dict: ...
```

- `places`: each `{"name": str, "path": str (absolute, already expanded), "owner": str, "depth": int, "repos": bool}`, in the order to walk.
- `deadline`: a `time.monotonic()` value. `progress`: a dict the walker updates in place: `progress["place"]` (name of the place being walked) and `progress["files"]` (files seen so far in the whole measurement).
- Returns `{"rows": [row], "skipped": [{"path", "reason"}], "skipped_count": int, "files": int, "complete": bool, "note": str | None}`.
- A `row` is `{"path", "parent", "place", "kind", "owner", "state", "bytes", "logical_bytes", "linked_bytes", "files", "changed_at"}`. `parent` is the path of the nearest folder above that has a row, `None` for a place. `changed_at` is `YYYY-MM-DDTHH:MM:SSZ` (UTC) or `None`.

**Rules the walker follows** (each has a test below):

1. One row of kind `place` for every place, always, also when it was not walked. Its `state`:
   - `missing`: the path does not exist. Zeros. Not in `skipped`.
   - `refused`: zeros, and one `skipped` entry with the reason. Reasons: `"guarded by macOS"` (when `guard.under(real path)` or `guard.under(given path)`; the real path is `os.path.realpath`), `"not a folder"`, `"overlaps <name>"` (the real path is inside, equal to, or contains the real path of an earlier place that was accepted).
   - `not_reached`: the deadline passed before this place started. Zeros.
   - `partial`: something below it was skipped or the deadline cut it.
   - `ok`: otherwise.
   The row's `path` is the real path.
2. Sizes. For a folder: `bytes` = its own `st_blocks * 512` plus that of every file and folder below. `logical_bytes` = `st_size` of every non-folder below. `files` = number of non-folders below (a symlink is one file, with its own `lstat` numbers). `changed_at` = newest `st_mtime` of the folder itself and everything below. All numbers come from `lstat` (`entry.stat(follow_symlinks=False)`).
3. A file with `st_nlink > 1` adds its `st_blocks * 512` to `linked_bytes` every time it is seen. It adds to `bytes` and `logical_bytes` only the first time its `(st_dev, st_ino)` is seen in the whole measurement (one set for all places). It always counts in `files`.
4. A symlink is never followed (`is_dir(follow_symlinks=False)`).
5. A folder whose `st_dev` differs from its place's root is not entered: `skipped` reason `"another volume"`.
6. Before a folder is opened: if `guard.hit(path)`, it is not opened: `skipped` reason `"guarded by macOS"`.
7. `scandir` fails: `PermissionError` gives reason `"permission denied"`; `FileNotFoundError` is ignored (the folder vanished); any other `OSError` gives its `strerror`. An `lstat` that fails on one entry: that entry is not counted; no `skipped` entry for `FileNotFoundError`, otherwise one with the `strerror`.
8. Every skip marks the folders above it, up to the place, `partial`.
9. Rows below a place: a folder gets a row when it is at most `depth` levels below the place root (kind `folder`), or when `repos` is true and it has a kind from this list, tested in this order, at any depth, as long as no folder above it (below the place) already has kind `deps`, `venv`, `build` or `git`:
   - it holds an entry named `.git`: `worktree` if `.git` is a file whose text contains `/worktrees/`, else `repo`
   - it holds a file `pyvenv.cfg`: `venv`
   - its name is in `DEPS`: `deps`
   - its name is in `BUILD`: `build`
   - its name is `.git`: `git`
   A folder within `depth` that has one of these kinds gets that kind instead of `folder`.
10. `owner` of every row is the place's `owner`.
11. The deadline is checked before each folder is opened. When it has passed: nothing more is opened; the rows of the folders being walked are still returned with what was counted, state `partial`; one `skipped` entry `{"path": <place path>, "reason": "time limit"}`; later places are `not_reached`; `complete` is `False` and `note` is `"time limit"`. Otherwise `complete` is `True` only when `skipped_count` is 0, and `note` is `None`.
12. `skipped` holds at most `MAX_SKIPPED` entries; `skipped_count` counts all.
13. No recursion: the walker keeps its own stack (a thread on macOS has a small C stack).
14. `Guard`: paths are compared case-insensitively (`str.casefold`). A pattern (`*` or `?` in the last part) matches folders with that parent whose name matches (`fnmatch.fnmatchcase` on casefolded names).
15. Entries of a folder are handled in name order, so two runs on the same tree give the same rows.

- [ ] **Step 1: Write the failing tests**

Start `tests/test_memory_management.py` with the same header pattern as `tests/test_project_management.py` (`REPO`, `sys.path.insert(0, str(REPO / "collector"))`, `from core import component, server`, `from core.app import Core`, `COMPS = REPO / "collector" / "components"`), then `MM = component.import_folder(COMPS / "memory_management")` and `W = MM.walk`.

Helpers in the test file:

```python
def place(path, name="t", depth=1, repos=False, owner="none"):
    return {"name": name, "path": str(path), "owner": owner, "depth": depth, "repos": repos}

def run(places, guard=None, seconds=30):
    return W.measure(places, guard or W.Guard([]), time.monotonic() + seconds, {})

def row(result, path):
    return next(r for r in result["rows"] if r["path"] == str(path))

def du(path):   # macOS du: 512-byte blocks, hard links once, symlinks not followed
    return int(subprocess.run(["du", "-s", str(path)], capture_output=True, text=True, check=True).stdout.split()[0]) * 512
```

Every test builds its tree under `Path(os.path.realpath(tempfile.mkdtemp()))` and removes it in cleanup (`shutil.rmtree(..., ignore_errors=True)` after restoring any `chmod`).

Class `WalkTest`, one test per line:

- `test_sizes_match_du_and_the_exact_logical_size`: tree `a/f1` (1000 bytes), `a/sub/f2` (5000 bytes), `a/sub/deep/f3` (70000 bytes). `r = run([place(a)])`. Assert `row(r, a)["bytes"] == du(a)`, `["logical_bytes"] == 76000`, `["files"] == 3`, `["kind"] == "place"`, `["state"] == "ok"`, `["parent"] is None`; `row(r, a/"sub")["logical_bytes"] == 75000`, `["kind"] == "folder"`, `["parent"] == str(a)`; no row for `a/sub/deep` (depth 1); `r["complete"] is True`, `r["files"] == 3`, `r["skipped"] == []`; `changed_at` matches `^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$`.
- `test_a_hard_linked_file_is_counted_once`: `a/x/f` (8192 bytes), `os.link` it to `a/y/g`. Assert place `logical_bytes == 8192`, `files == 2`, `bytes == du(a)`; `row(x)["logical_bytes"] == 8192` and `row(y)["logical_bytes"] == 0`; both `linked_bytes == os.lstat(f).st_blocks * 512`; place `linked_bytes` is twice that.
- `test_two_places_share_one_set_of_hard_links`: `p1/f` linked to `p2/g` (4096 bytes). `run([place(p1, "one"), place(p2, "two")])`: `row(p1)["logical_bytes"] == 4096`, `row(p2)["logical_bytes"] == 0`.
- `test_a_symlink_loop_and_a_link_out_are_not_followed`: `a/sub/loop -> a`, `a/out -> other` where `other/big` is 50000 bytes, `a/f` 10 bytes. Assert it returns, `files == 3`, `logical_bytes == 10 + os.lstat(a/"sub"/"loop").st_size + os.lstat(a/"out").st_size`, no row path contains `other`, `complete is True`.
- `test_an_unreadable_folder_is_listed_and_the_rest_is_measured`: `a/ok/f` (100), `a/locked/f` (100), `os.chmod(a/"locked", 0)` (restore to `0o700` in cleanup). Assert `{"path": str(a/"locked"), "reason": "permission denied"} in r["skipped"]`, `r["skipped_count"] == 1`, `row(a)["state"] == "partial"`, `row(a/"ok")["state"] == "ok"`, `row(a/"ok")["logical_bytes"] == 100`, `r["complete"] is False`, `r["note"] is None`.
- `test_a_guarded_folder_is_never_opened`: `a/Documents/secret`, `a/Library/Caches/com.apple.x/f`, `a/Library/Caches/pip/f`. Guard `W.Guard([str(a/"Documents"), str(a/"Library"/"Caches"/"com.apple.*")])`. Replace `W.scandir` with a wrapper that records each path it is given (`mock.patch.object(W, "scandir", ...)`). Assert neither guarded path was given to it, both are in `skipped` with `"guarded by macOS"`, and `pip` was opened. Use `place(a, depth=3)`.
- `test_a_place_that_is_guarded_by_another_spelling_is_refused` (Review Focus 5): guard `[str(a/"Documents")]`; places `a/"documents"/"x"` (create `a/Documents/x`; skip this half with `self.skipTest` if `os.path.exists(a/"documents")` is false, i.e. a case-sensitive disk) and a symlink `a/link -> a/Documents`. Both rows: `state == "refused"`, `bytes == 0`; `skipped` has `"guarded by macOS"` for each; `W.scandir` was never called (patched recorder).
- `test_missing_not_a_folder_and_overlapping_places`: places `a` (name "one"), `a/sub` (name "two"), `a/nope` (name "three"), `a/f1` a file (name "four"). States `ok`, `refused`, `missing`, `refused`; skipped reasons `"overlaps one"` and `"not a folder"`; `missing` is not in `skipped`; four place rows in this order.
- `test_kinds_in_a_repos_place`: under `code/`: `r1/.git/HEAD`, `r1/src/a.py`, `r1/node_modules/p/node_modules/q/f`, `r1/node_modules/p/dist/f`, `r1/.venv/pyvenv.cfg`, `r1/web/.next/f`, `r1/.claude/worktrees/w1/.git` (a file with text `gitdir: /x/r1/.git/worktrees/w1\n`), `r1/.claude/worktrees/w1/build/f`, `group/r2/.git/HEAD`, `plain/f`. `run([place(code, depth=1, repos=True, owner="project_management")])`. Assert the set of `(relative path, kind)` of all rows is exactly `{(".", "place"), ("r1", "repo"), ("r1/.git", "git"), ("r1/node_modules", "deps"), ("r1/.venv", "venv"), ("r1/web/.next", "build"), ("r1/.claude/worktrees/w1", "worktree"), ("r1/.claude/worktrees/w1/build", "build"), ("group", "folder"), ("group/r2", "repo"), ("group/r2/.git", "git"), ("plain", "folder")}`; `row(r1/"node_modules")["parent"] == str(r1)`; `row(w1/"build")["parent"] == str(w1)`; every row's `owner == "project_management"`.
- `test_the_same_names_are_plain_folders_without_repos`: same idea, small: `a/node_modules/f`, `a/x/.git/HEAD` with `place(a, depth=1)`: kinds are `place`, `folder`, `folder` and nothing below.
- `test_the_time_limit_stops_the_walk_and_keeps_what_it_has`: two places `p1` (with `p1/s1/f`, `p1/s2/f`) and `p2` (with `p2/f`). Patch `W.scandir` with a wrapper that calls the real one and, on its second call (that is `p1/s1`), first sleeps 0.4 s. Call `W.measure([place(p1, "one"), place(p2, "two")], W.Guard([]), time.monotonic() + 0.2, {})`. Assert `r["complete"] is False`, `r["note"] == "time limit"`, `row(p1)["state"] == "partial"`, `row(p1 / "s1")["files"] == 1`, `row(p2)["state"] == "not_reached"`, `row(p2)["bytes"] == 0`, `{"path": str(p1), "reason": "time limit"} in r["skipped"]`, and the wrapper was called exactly twice.
- `test_a_folder_that_vanishes_is_not_an_error` (Review Focus 1): patch `W.scandir` so the call for `a/gone` raises `FileNotFoundError`. Assert `r["skipped"] == []`, `r["complete"] is True`, `row(a)["state"] == "ok"`.
- `test_a_very_deep_tree_needs_no_recursion` (Review Focus 4): build 400 nested folders named `d` under `a` (`a/d/d/d/…`, one `os.mkdir` per level; the whole path stays under 1,000 characters) with one file at the bottom. Around the call only, set `sys.setrecursionlimit(len(inspect.stack()) + 150)` and restore the old limit in a `finally`. Assert it returns, `row(a)["state"] == "ok"` and `row(a)["files"] == 1`.
- `test_more_skips_than_the_cap_are_counted_not_listed`: `mock.patch.object(W, "MAX_SKIPPED", 2)`, three unreadable folders: `len(r["skipped"]) == 2`, `r["skipped_count"] == 3`.
- `test_progress_is_reported`: `p = {}`; `W.measure([place(a)], W.Guard([]), time.monotonic() + 30, p)`; `p == {"place": "t", "files": 3}` for a tree with 3 files.

- [ ] **Step 2: Run the tests, see them fail**

Run: `python3 -m unittest tests.test_memory_management -v`
Expected: an error at import (`walk` has no `measure`), or every test fails.

- [ ] **Step 3: Write `walk.py`** to the interface and the 15 rules above.

Shape (this is the part the rules do not fix): one loop over a list used as a stack. Each stack item is a node for one folder with its totals, its list of sub-folders still to visit, its row kind (or `None`), the path of the nearest row above, whether a labelled folder is above, and a `partial` flag. Visiting a node the first time: check deadline, guard and `scandir`; sort the entries by name; count the files; make child nodes. When a node has no children left: pop it, add its totals and its `partial` flag to the node below, and append its row if it has a kind.

- [ ] **Step 4: Run the tests, see them pass**

Run: `python3 -m unittest tests.test_memory_management -v`
Expected: all pass, no other output.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -5`
Expected: `OK`, 224 plus the new tests, and no line of output that the suite did not print before.

- [ ] **Step 6: Commit**

```bash
git add collector/components/memory_management tests/test_memory_management.py
git commit -m "Memory management: the walker that measures folders, read only"
```

---

### Task 2: The records

**Files:**
- Create: `collector/components/memory_management/records.py`
- Modify: `collector/components/memory_management/__init__.py` (import `records`; `COMPONENT = Component(name="memory_management", records=records.RECORDS)`)
- Test: `tests/test_memory_management.py` (add class `RecordsTest`)

**Interfaces:**
- Consumes: the result dict of `walk.measure` (Task 1).
- Produces, in `records.py`:

```python
RECORDS = [
    "CREATE TABLE IF NOT EXISTS memory_management_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, "
    "finished_at TEXT, finished_ts REAL, seconds REAL, complete INTEGER, note TEXT, disk_path TEXT, "
    "disk_total INTEGER, disk_free INTEGER, files INTEGER, skipped INTEGER)",
    "CREATE TABLE IF NOT EXISTS memory_management_folders (run INTEGER, path TEXT, parent TEXT, place TEXT, kind TEXT, "
    "owner TEXT, state TEXT, bytes INTEGER, logical_bytes INTEGER, linked_bytes INTEGER, files INTEGER, changed_at TEXT)",
    "CREATE TABLE IF NOT EXISTS memory_management_skipped (run INTEGER, path TEXT, reason TEXT)",
]

def save(db, run: dict, result: dict) -> None: ...
def last(db) -> dict | None: ...
def last_finished_ts(db) -> float | None: ...
```

- `run`: `{"started_at": str, "finished_at": str, "finished_ts": float, "seconds": float, "disk": {"path": str, "total": int, "free": int} | None}`.
- `save` writes one run row, every row of `result["rows"]` and every entry of `result["skipped"]`, then deletes the folder and skipped rows of every other run. Run rows are never deleted. It does not commit or close: the caller does (`with closing(ctx.db()) as db, db:`), so a failure writes nothing.
- `last` returns `None` when there is no run, else `{"run": {...}, "rows": [...], "skipped": [...]}` for the run with the highest id. `run` has the keys `started_at, finished_at, seconds, complete (bool), note, disk (the dict or None), files, skipped (the count)`. `rows` are dicts with exactly the row keys of Task 1, in the order they were saved. `skipped` are `{"path", "reason"}`.
- `last_finished_ts` returns `finished_ts` of the newest run, or `None`.

- [ ] **Step 1: Write the failing tests**

Class `RecordsTest`. `setUp`: a temp home, `core = Core(home, home / ".claude", COMPS)`, `self.assertIsNone(core.error)`, `self.db = lambda: core.ctx("memory_management", {}).db()`.

- `test_the_component_creates_its_three_tables`: the names in `sqlite_master` that start with `memory_management_` are exactly `{"memory_management_runs", "memory_management_folders", "memory_management_skipped"}`, and `[c.error for c in core.components if c.name == "memory_management"] == [None]`.
- `test_nothing_saved_gives_none`: `R.last(db) is None`, `R.last_finished_ts(db) is None`.
- `test_save_then_last_gives_the_same_measurement`: build `result` by hand: two rows (a place row with `parent None` and a folder row), one skipped entry, `skipped_count 5`, `files 7`, `complete False`, `note "time limit"`; `run` with `disk {"path": "/x", "total": 100, "free": 40}`, `finished_ts 1000.5`, `seconds 2.5`. After `save`: `last()["rows"] == result["rows"]`, `["skipped"] == result["skipped"]`, `["run"] == {"started_at": ..., "finished_at": ..., "seconds": 2.5, "complete": False, "note": "time limit", "disk": {"path": "/x", "total": 100, "free": 40}, "files": 7, "skipped": 5}`, `last_finished_ts() == 1000.5`.
- `test_a_run_without_disk_numbers`: `run["disk"] = None` gives `last()["run"]["disk"] is None`.
- `test_only_the_last_runs_folders_are_kept_but_every_run_row`: save twice with different rows; `SELECT count(*) FROM memory_management_runs` is 2; the folders and skipped tables hold only the second run's rows; `last()` is the second.
- `test_a_failed_save_writes_nothing`: call `save` inside `with closing(self.db()) as db, db:` with a `result` whose second row lacks the key `"bytes"`; `assertRaises(KeyError)`; afterwards `last()` is still `None`.

- [ ] **Step 2: Run, see them fail**

Run: `python3 -m unittest tests.test_memory_management.RecordsTest -v`
Expected: fail, `records` does not exist.

- [ ] **Step 3: Write `records.py`**, and add `records=records.RECORDS` to `COMPONENT`.

- [ ] **Step 4: Run, see them pass**

Run: `python3 -m unittest tests.test_memory_management -v`
Expected: all pass, no other output.

- [ ] **Step 5: Commit**

```bash
git add collector/components/memory_management tests/test_memory_management.py
git commit -m "Memory management: its three tables, save and read of the last measurement"
```

---

### Task 3: The reader, the background measurement and the route

**Files:**
- Modify: `collector/components/memory_management/__init__.py`
- Test: `tests/test_memory_management.py` (add classes `SettingsTest` and `ComponentTest`)

**Interfaces:**
- Consumes: `walk.Guard`, `walk.measure`, `walk.GUARDED_HOME`, `walk.GUARDED_ABS` (Task 1); `records.save`, `records.last`, `records.last_finished_ts`, `records.RECORDS` (Task 2); from the core `core.component.Component`, `core.util.now_iso`, `core.app.StoreError`; `ctx.cfg`, `ctx.home`, `ctx.db()`.
- Produces, in `__init__.py`:

```python
DEFAULT_PLACES = [   # (name, path, owner, depth, repos); "~" is the home folder
    ("code", "~/code", "project_management", 1, True),
    ("claude", "<claude dir>", "claude_management", 2, False),   # os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"
    ("claude_app", "~/Library/Application Support/Claude", "claude_management", 1, False),
    ("stellaxis", "<ctx.home>", "stellaxis", 1, False),
    ("knowledge", "~/Knowledge", "none", 2, False),
    ("archive", "~/Archive", "none", 1, False),
    ("xcode", "~/Library/Developer", "none", 3, False),
    ("android_sdk", "~/Library/Android", "none", 2, False),
    ("android", "~/.android", "none", 1, False),
    ("caches", "~/Library/Caches", "none", 1, False),
    ("homebrew", "/opt/homebrew", "none", 1, False),
    ("local", "~/.local", "none", 2, False),
    # then, all ("<name>", "<path>", "none", 1, False):
    # pnpm ~/Library/pnpm, npm ~/.npm, bun ~/.bun, yarn ~/.yarn, cache ~/.cache, rustup ~/.rustup, cargo ~/.cargo,
    # m2 ~/.m2, gradle ~/.gradle, cocoapods ~/.cocoapods, nvm ~/.nvm, ollama ~/.ollama, docker ~/.docker, vscode ~/.vscode
]

def duration(text, default: float) -> float: ...     # "90s", "15m", "6h", "2d" or a number of seconds; anything else: default
def settings(cfg: dict, stx_home) -> dict: ...       # {"every": float, "max_seconds": float, "places": [place], "guard": walk.Guard, "idle": str | None}
def measure_reader(ctx) -> None: ...
def answer(ctx) -> dict: ...
def handle(handler, method: str, ctx) -> bool: ...
_JOBS: dict = {}     # str(ctx.home) -> {"thread": threading.Thread | None, "started_at": str | None, "progress": dict, "last_try": float | None, "error": str | None}
COMPONENT = Component(name="memory_management", readers=[measure_reader], records=records.RECORDS, handle=handle)
```

**`settings(cfg, stx_home)`**

- `m = cfg.get("memory")`; anything that is not a dict counts as `{}`.
- `every = duration(m.get("every"), 6 * 3600)`, but never below 60. `max_seconds = duration(m.get("max_seconds"), 900)`, never below 1.
- Home is `account_home()`: `Path(pwd.getpwuid(os.getuid()).pw_dir)`, read at call time. Not `Path.home()`, which follows `$HOME` (changed while building; see "Found while building step 5").
- The defaults are used only when `stx_home` (resolved) is inside the home folder (resolved). Otherwise the list starts empty.
- `m.get("places")` (a list; anything else counts as `[]`) is merged by `name`: an entry replaces the default of that name, in place; a new name is added at the end; an entry with `"off": true` removes that name. An entry that is not a dict, has no string `name`, or (when not `off`) no string `path` is dropped. `owner` defaults to `"none"`, `depth` to 1 (an int from 0 to 6; anything else is 1), `repos` to `False`.
- Every `path` is expanded with the home folder (`~` and `~/…`) and made absolute.
- `guard = walk.Guard(home/each GUARDED_HOME + GUARDED_ABS + each string of m.get("guarded") expanded the same way)`.
- `idle` is `None` when there is at least one place, else exactly `"no places to measure: the Stellaxis data folder is outside the home folder and config names no places"`.
- It never raises on bad config.

**`measure_reader(ctx)`** — runs inside the collection pass, so it must return at once:

1. `s = settings(ctx.cfg, ctx.home)`. No places: return (before the store is touched).
2. Under a module lock, get or make the job of `str(ctx.home)`. If its thread is alive: return.
3. If `last_try` is `None`, set it from `records.last_finished_ts` (through `ctx.db()`, closed after). A `StoreError` is not caught here: the core lists it as this reader's error.
4. If `last_try` is not `None` and `time.time() - last_try < s["every"]`: return.
5. Set `last_try = time.time()`, `started_at = now_iso()`, `progress = {}`, and start one daemon thread named `memory-measure` that:
   - reads the disk with `os.statvfs(ctx.home)`: `total = f_frsize * f_blocks`, `free = f_frsize * f_bavail`, `path = str(ctx.home)`; on `OSError` the disk is `None`;
   - calls `walk.measure(s["places"], s["guard"], time.monotonic() + s["max_seconds"], progress)`, looked up on the `walk` module at call time (tests replace `walk.measure`), never through `from .walk import measure`;
   - if `ctx.home / "stellaxis.db"` does not exist, stops there without writing (the home was removed; `ctx.db()` would create a new file);
   - else saves with `records.save` inside `with closing(ctx.db()) as db, db:`;
   - on any exception stores `"<Type>: <message>"` in the job's `error`; on success sets it to `None`. It never prints and never raises out of the thread.

**`answer(ctx)`** returns exactly these keys:

```python
{"state": "never" | "measured", "measured_at": str | None, "started_at": str | None, "seconds": float | None,
 "complete": bool | None, "note": str | None,
 "measuring": None | {"started_at": str, "place": str | None, "files": int},
 "disk": None | {"path": str, "total": int, "free": int, "used": int},
 "places": [row], "folders": [row], "elsewhere_bytes": int | None,
 "not_measured": [{"path", "reason"}], "not_measured_count": int,
 "idle": str | None, "error": str | None}
```

`places` are the rows of kind `place`, `folders` all other rows. `measured_at` is the run's `finished_at`. `elsewhere_bytes` is `disk used - sum of the places' bytes` (`None` without disk numbers). `measuring` is not `None` only while this home's thread is alive. `error` is the job's error. `idle` comes from `settings`. Before the first save, `state` is `"never"`, the lists are empty and the rest is `None` (`not_measured_count` 0).

**`handle(handler, method, ctx)`**: only `GET` on the path `/api/memory` (query string ignored) is answered: 200 with `answer(ctx)` as JSON, headers `Content-Type: application/json`, `Cache-Control: no-store`, `Content-Length`. A `StoreError` gives 503 with `{"error": "<message>"}`. Everything else returns `False`.

- [ ] **Step 1: Write the failing tests**

Class `SettingsTest` (no Core needed; `home = realpath(mkdtemp())`):

- `test_defaults_are_off_when_the_data_folder_is_outside_the_home_folder`: `s = MM.settings({}, home / "stx")` (the real `Path.home()` does not contain a temp folder): `s["places"] == []`, `s["idle"]` is the exact sentence above, `s["every"] == 21600`, `s["max_seconds"] == 900`.
- `test_defaults_are_on_inside_the_home_folder`: with `mock.patch.dict(os.environ, {"HOME": str(home)})`, and `CLAUDE_CONFIG_DIR` removed from the environment for the test: `s = MM.settings({}, home / ".stellaxis")`; names start with `["code", "claude", "claude_app", "stellaxis"]` and include `"homebrew"`; the `code` place is `{"name": "code", "path": str(home / "code"), "owner": "project_management", "depth": 1, "repos": True}`; `claude` path is `str(home / ".claude")`; `stellaxis` path is `str(home / ".stellaxis")`; `s["idle"] is None`; `s["guard"].hit(str(home / "Documents"))`, `s["guard"].hit(str(home / "Library" / "Caches" / "com.apple.Safari"))`, `s["guard"].hit("/Volumes")` are all true.
- `test_config_places_add_replace_and_remove`: same patched home; cfg `{"memory": {"places": [{"name": "chrome", "path": "~/Library/Application Support/Google"}, {"name": "code", "path": "~/work", "depth": 2, "repos": True, "owner": "project_management"}, {"name": "homebrew", "off": True}], "guarded": ["~/Private"]}}`: `code` is still first with path `home/work` and depth 2; `chrome` is last with owner `"none"`, depth 1, repos `False`; no `homebrew`; `guard.hit(str(home / "Private"))`.
- `test_bad_config_falls_back` (Review Focus 3): for each of `{"memory": 5}`, `{"memory": {"every": "soon", "max_seconds": [], "places": "x", "guarded": 7}}`, `{"memory": {"places": [3, {"name": "a"}, {"path": "/x"}, {"name": "ok", "path": str(home), "depth": "x"}]}}`: no exception; the first two give `every == 21600` and `max_seconds == 900`; the third gives exactly one place, `ok`, with depth 1 (use `home / "stx"` outside the home folder so defaults are off).
- `test_durations`: `MM.duration("90s", 1) == 90`, `"15m"` 900, `"6h"` 21600, `"2d"` 172800, `120` 120, `"x"` and `None` give the default; `settings({"memory": {"every": "1s"}}, …)["every"] == 60`.

Class `ComponentTest`. `setUp`: `home = realpath(mkdtemp()) / "stx"` (created), a tree `tree/` with `f1` (1000 bytes) and `sub/f2` (5000 bytes), `self.cfg = {"workspaces": [], "claude_bin": "/no/claude", "memory": {"places": [{"name": "t", "path": str(tree)}]}}`, `self.core = Core(home, home / ".claude", COMPS)`. Helpers: `job()` returns `MM._JOBS[str(home)]`; `wait()` joins `job()["thread"]` with a 20 s timeout and asserts it is not alive; `get(path)` starts `server.serve(self.core, 0, self.cfg)` once (thread, cleanup `shutdown` and `server_close`) and returns `(status, json)`. Cleanup: join any live job thread, pop `str(home)` from `MM._JOBS`, remove the temp folder.

A gate for slow walks: `gate = threading.Event()`; `real = W.scandir`; `def slow(p): gate.wait(20); return real(p)`; `mock.patch.object(W, "scandir", slow)`; add `gate.set` as a cleanup **before** the join cleanup runs.

- `test_the_component_loads_in_a_real_core_and_api_core_lists_it`: `self.core.describe()` has an entry `{"name": "memory_management", "readers": 1, "actions": [], "offers": [], "reads": [], "error": None}`; `GET /api/core` gives 200 and the same entry; `self.core.error is None`.
- `test_a_pass_measures_in_the_background_and_the_route_shows_it`: `res = self.core.run_pass(dict(self.cfg))`; `res["components"]["memory_management"] == {"readers": 1, "errors": []}`; `wait()`; `status, a = get("/api/memory")`; `status == 200`; `a["state"] == "measured"`; `a["complete"] is True`; `[p["name"]... ]`: `a["places"][0]["path"] == str(tree)`, `["logical_bytes"] == 6000`, `["files"] == 2`, `["state"] == "ok"`; `[f["path"] for f in a["folders"]] == [str(tree / "sub")]`; `a["measuring"] is None`; `a["error"] is None`; `a["idle"] is None`; `a["disk"]["used"] == a["disk"]["total"] - a["disk"]["free"]`; `a["elsewhere_bytes"] == a["disk"]["used"] - a["places"][0]["bytes"]`; `a["not_measured"] == []`; `a["measured_at"]` matches the ISO pattern; `set(a)` equals the 15 keys of `answer`.
- `test_a_slow_walk_does_not_block_the_pass_and_the_route_answers_before_it_is_ready`: with the gate closed: `t = time.monotonic(); self.core.run_pass(dict(self.cfg)); self.assertLess(time.monotonic() - t, 1.0)`; `job()["thread"].is_alive()`; `status, a = get("/api/memory")` gives 200 with `a["state"] == "never"`, `a["measured_at"] is None`, `a["places"] == []`, `a["measuring"]["started_at"]` matching the ISO pattern, `a["measuring"]["files"] == 0`. Then `gate.set(); wait()`; the route now says `"measured"`.
- `test_a_second_pass_does_not_start_a_second_measurement`: gate closed; count the starts of the measuring thread: `real_thread = threading.Thread`, `starts = []`, and `mock.patch.object(MM.threading, "Thread", side_effect=lambda *a, **k: (starts.append(1) if k.get("name") == "memory-measure" else None, real_thread(*a, **k))[1])` (so `__init__.py` must create it as `threading.Thread(target=…, name="memory-measure", daemon=True)`); run three passes; `len(starts) == 1`; `gate.set(); wait()`; one more pass: still 1 (the result is fresh), and `SELECT count(*) FROM memory_management_runs` is 1.
- `test_it_measures_again_only_when_the_last_one_is_older_than_every`: pass, `wait()`; `job()["last_try"] -= 5 * 3600`; pass: thread not alive, runs table has 1 row; `job()["last_try"] -= 2 * 3600`; pass, `wait()`: 2 rows.
- `test_a_new_process_reads_the_age_from_the_store`: pass, `wait()`; `MM._JOBS.pop(str(home))` (as after a restart); pass: no thread was started (`job()["thread"] is None`), and `job()["last_try"]` is within 60 s of `time.time()`.
- `test_no_places_means_no_measurement_and_the_route_says_why`: `cfg` without `memory`; pass; `str(home) not in MM._JOBS or MM._JOBS[str(home)]["thread"] is None`; the route gives 200, `state == "never"`, `idle` is the exact sentence, `measuring is None`.
- `test_a_measurement_that_fails_is_reported_and_not_retried_at_once`: `mock.patch.object(W, "measure", side_effect=RuntimeError("boom"))`; pass; `wait()`; `get("/api/memory")[1]["error"] == "RuntimeError: boom"`; count thread starts as above over two more passes: 0.
- `test_the_home_is_removed_while_it_measures` (Review Focus 2): gate closed; pass; `shutil.rmtree(home)`; `gate.set(); wait()`; `not home.exists()`; capture `sys.stderr` and `sys.stdout` around the whole test with `contextlib.redirect_stderr/redirect_stdout` into `io.StringIO` and assert both are empty.
- `test_the_route_with_the_store_down`: `(broken / "stellaxis.db").mkdir(parents=True)`; `core = Core(broken, …, COMPS)`; `core.error` is set; serve it; `GET /api/memory` gives 503 and a JSON body with an `error` string; nothing was printed (redirect as above). A pass on that core with the `memory` places config lists one error for `memory_management` that starts with `"StoreError"`, and starts no thread.
- `test_other_paths_and_methods_are_not_ours`: `GET /api/memoryx` and `POST /api/memory` give 404 from `server.serve`.
- `test_the_component_imports_only_the_core_and_itself`: parse every `.py` file in `collector/components/memory_management/` with `ast`; every imported top-level module name is in `sys.stdlib_module_names` (when that attribute exists; else skip the stdlib check) or is `core` or is a relative import; none is `collect`, `workspaces`, `unplaced` or `prune`.
- `test_no_code_changes_a_file`: the source text of the three files contains none of `os.remove`, `os.unlink`, `os.rmdir`, `os.rename`, `os.replace`, `shutil`, `os.chmod`, `open(`, `.write_text`, `.unlink(`, `.mkdir(`, `subprocess`. (`walk.py` must read a worktree's `.git` file: do it with `os.open`/`os.read`/`os.close` in read-only mode, at most 512 bytes, so the rule "no `open(`" can hold. `os.open(` contains `open(`; so the test removes the substring `os.open(` before it looks for `open(`.)

- [ ] **Step 2: Run, see them fail**

Run: `python3 -m unittest tests.test_memory_management -v`
Expected: the new classes fail (`settings` is missing).

- [ ] **Step 3: Write the rest of `__init__.py`** to the interface above.

- [ ] **Step 4: Run this file, see it pass**

Run: `python3 -m unittest tests.test_memory_management -v`
Expected: all pass, no other output, the file takes under 5 seconds.

- [ ] **Step 5: Run the whole suite three times**

Run: `for i in 1 2 3; do python3 -m unittest discover -s tests 2>&1 | tail -4; done`
Expected: `OK` each time, and no warnings or tracebacks. (Other test files now load this component in every real `Core`; with their temp homes the defaults are off, so it must start no thread there.)

Also run with the system Python: `/usr/bin/python3 -m unittest tests.test_memory_management 2>&1 | tail -3`. Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add collector/components/memory_management tests/test_memory_management.py
git commit -m "Memory management: measure in the background, GET /api/memory"
```

---

### Task 4: The document bullet and the last checks

**Files:**
- Modify: `CLAUDE.md` (one new bullet at the end of the Architecture list, after the `collector/cloud_import.py` bullet)
- Modify: this plan (append the section "Found while building step 5")

- [ ] **Step 1: Add this bullet to `CLAUDE.md`**, wrapped at the file's line length:

```
- `collector/components/memory_management/` is Memory management, read side only: it measures disk space and never
  deletes, moves or changes a file. `walk.py` walks the places (never follows a symlink, never enters another volume
  or a folder macOS guards, counts a hard-linked file once); `records.py` keeps the last measurement in
  `memory_management_runs`, `_folders` and `_skipped`; the reader only checks the clock and starts one background
  thread when the last measurement is older than `memory.every` (default 6h). Route: `GET /api/memory`. Config key
  `memory`: `every`, `max_seconds`, `places` (added to the defaults by name), `guarded` (only adds). The default
  places are off when `$STELLAXIS_HOME` is outside the home folder, so tests never walk the real disk. The design,
  with what cleaning may ever offer, is `docs/superpowers/specs/2026-11-11-memory-management-design.md`.
```

- [ ] **Step 2: One read-only check against the real disk**, with the store in a temp folder so nothing real is written. Run from the repo root:

```bash
python3 - <<'EOF'
import os, sys, tempfile, time, json
from pathlib import Path
sys.path.insert(0, "collector")
from core.app import Core
from core import component
home = Path(tempfile.mkdtemp()) / "stx"; home.mkdir()
core = Core(home, Path.home() / ".claude", Path("collector/components"))
MM = component.import_folder(Path("collector/components/memory_management"))
cfg = {"workspaces": [], "claude_bin": "/no/claude",
       "memory": {"places": [{"name": "code", "path": "~/code", "repos": True, "owner": "project_management"},
                             {"name": "claude", "path": "~/.claude", "depth": 2}]}}
t = time.monotonic(); core.run_pass(cfg); print("pass took %.3fs" % (time.monotonic() - t))
MM._JOBS[str(home)]["thread"].join(600)
a = MM.answer(core.ctx("memory_management", cfg))
print(a["state"], a["seconds"], a["complete"], a["error"], len(a["folders"]), a["not_measured_count"])
for p in a["places"]: print(p["name"], round(p["bytes"] / 1e9, 2), "GB", p["files"], p["state"])
EOF
```

Expected: the pass takes under 0.1 s; `measured`; `~/code` near 26 GB and `~/.claude` near 4 GB; `error` is `None`. Write the numbers into the section of Step 3.

- [ ] **Step 3: Append "Found while building step 5"** to this plan: what the build found that the design or the core should know (limits, surprises, the numbers of Step 2).

- [ ] **Step 4: Run the whole suite**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -4`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/superpowers/plans/2026-11-11-core-step-5-memory-read-side.md
git commit -m "Docs: Memory management in CLAUDE.md; what the build of step 5 found"
```

## Found while building step 5

- **The home folder is the account's, not `$HOME`.** The plan first said `Path.home()`. The review of Task 3 found that `tests/test_e2e.py` and `tests/test_relocate.py` run `collect.py` with a false `HOME` and the data folder inside it. The defaults then turned on, and a background thread began to walk the real `/opt/homebrew`. Now `settings` asks macOS for the account's home (`pwd`). A false `HOME` gives no places. Two tests pin this.
- **`collect.py run` starts a measurement it cannot finish.** It makes one pass, then the process ends, and the thread ends with it. Nothing is saved, so the next `run` starts again. Only the service finishes a measurement. The core has no way to tell a reader "this is a single pass".
- **The reader reads the store one time while it holds its lock.** It happens once for each data folder in each process, to learn the age of the last measurement. SQLite waits at most 5 seconds. In practice it is a read of one row.
- **A measurement that hangs cannot be stopped.** It is a thread. If macOS ever showed a permission prompt in spite of the guarded list, that thread would wait until the service restarts. The pass and the routes keep working, and `/api/memory` shows `measuring` with an old start time.
- **What the core should get for this component:** a rule for changing a table (today tables can only be created); a way for a reader to do slow work (this component brings its own thread); Claude's folder on `Ctx` (this component reads `CLAUDE_CONFIG_DIR` itself); a way to know that a pass is the only one of its process.
- **What the other components must offer before any cleaning:** Project management: which project owns a folder, and whether it has work in use. Claude management: which transcripts, skills, plugin files and archives are in use.
- **Not tested:** the "another volume" rule. A test cannot mount a disk.
- **Small things left open** are listed in the final review of this branch, not here.
- **Measured on this Mac** (real disk, store in a temp folder, 2026-11-11):
  - The pass took 0.003 s. The measurement took 28.4 s. State `measured`, complete, `error` is `None`.
  - `~/code`: 26.21 GB on disk (23.68 GB logical, 0.25 GB counted once through hard links), 896,505 files.
  - `~/.claude`: 4.32 GB on disk (4.28 GB logical), 15,296 files.
  - 808 folder rows. 0 folders could not be measured.
  - The check script in Task 4 Step 2 prints `p["name"]`, but a place row has the key `place`. The script stops with a `KeyError` after printing the first line; the numbers above come from the same run with the key fixed.
