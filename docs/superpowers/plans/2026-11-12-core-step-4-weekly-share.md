# Core step 4: the weekly share of the Claude plan (warn only). Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude management knows, for each week, how much of the owner's weekly budget each project used, and makes one warning when a project has used all of its share. It never stops work.

**Architecture:** Claude management gets a third reader. In round three it adds up the cost of each project for the week that is running, from the hourly costs its own store already holds and from what Project management offers as `sessions`. The budget, the week start and the percents are records in the store, changed only by three actions (dry run, manifest, undo). One route of its own, `GET /api/share`, gives the numbers; the project page and Home each show one line from it.

**Tech Stack:** Python 3 standard library only (must run on Python 3.9), SQLite through `sqlite3`, plain HTML and JavaScript pages. No packages, no build step.

**Spec:** `docs/superpowers/specs/2026-11-11-core-architecture-design.md` (section "3. Claude management", "Weekly share rules"). The agreed words: `CONTEXT.md`. The rules of a component: `collector/components/README.md`. The 17 points this plan starts from: the end of `docs/superpowers/plans/2026-11-11-core-step-3b-claude-management.md`.

## The owner's answers (2026-11-12) and the 17 points

| Point | Answer |
|---|---|
| 1. How a warning reaches the owner | "Shown on the pages." One line on the project page and one in Home's "needs you" list. No Mac notification, no Jarvis speech, no hook. |
| 2. Where a warning is kept | Table `claude_management_share_warnings`, one row for each project and week. |
| 3. The real weekly limit | Not known. Nothing here reads it. The owner sets one weekly budget in USD. |
| 4. When a week starts | "Friday 2:30 AM", read as India time (+05:30, the Mac's zone; the main session's call). That is Thursday 21:00 UTC, a whole UTC hour. It is the first value, kept as a default in code; the owner changes it with an action. |
| 5. Hourly costs | Used as they are (whole UTC hours). A week start that is not on a whole UTC hour is refused with a plain sentence. No half-hour parts. |
| 6. Who adds up | Claude management owns the share, as the spec says. It reads `project_management.sessions` and `project_management.projects`. |
| 7. The clock | `core.util.now()` everywhere. Tests fix it with `STELLAXIS_NOW`. |
| 8. Rounds | The share reader is the **third** reader of Claude management. In round three it sees the sessions Project management wrote in round two of the same pass. So the first pass after the merge already shows the right use. |
| 9. Schema | Five new tables, added to `records` and as the first step of `Component.changes`. No shipped `CREATE TABLE` line is edited. |
| 10. The action | Three actions of Claude management: `set_budget`, `set_week_start`, `set_percent`. One undo step, `claude_management.share_setting`. The values live in tables of the component, not in `config.json`. |
| 11. The reference test | The share has a route of its own. `snapshot.json`, `status.json`, `cache.json` and `GET /api/projects` get no new key. `tests/test_same_output.py`, its harness and its expected file are not edited. |
| 12. Warn only | A warning is a record and a line on two pages. Nothing blocks, nothing hooks. `status.json` is not used for it. |
| 13. Unassigned | Its use is its own line. It has no share unless the owner gives the id `unassigned` a percent. |
| 14. The undo is logged | Task 1: an applied `restore` writes one line to `actions.jsonl`. |
| 15. The checks | The remove-one tests get the share: with Project management removed, `GET /api/share` answers with no project lines; with Claude management removed it answers 404. |
| 16, 17 | Not in this step. |

## Global Constraints

- Python 3 standard library only. Every new file starts with `from __future__ import annotations` and must run on Python 3.9.
- A component imports only the standard library, `core` and its own folder. A file of Claude management never holds the text `project_management_` or `memory_management_` (a test fails on it), also not in a comment.
- A component makes only tables named `claude_management_…`.
- Never edit `tests/test_same_output.py`, `tests/same_output_harness.py` or `tests/same_output_expected.json`. Never add a key to `snapshot.json`, `status.json`, `cache.json` or the answer of `GET /api/projects`.
- Never edit anything in `collector/components/memory_management/` (another session works there).
- Every change the product makes is a dry run until applied. An applied action writes a manifest under `~/.claude/_archive/<stamp>-<label>/` and one line in `actions.jsonl`.
- Stellaxis warns and never stops work: no hook, no block, no notification outside the pages.
- Tests never touch the real `~/.stellaxis` or `~/.claude`: an in-process test uses `fake_home` from `tests/test_hardening.py`, gives a config with `"scan_roots": []`, and patches `live` and `app_records` of the command centre. A child process gets a fake `HOME` and `STELLAXIS_HOME`.
- Get a component's module in tests with `core.component.import_folder(...)`, never by another import.
- The test output must be clean: `python3 -X dev -m unittest discover -s tests` prints no warning and no stray line.
- Pages: read `DESIGN.md` first. Colours only through tokens. `home.html` has no buttons and no inputs. Text on a page goes through `esc()`.
- Words in code comments, messages and documents follow `CONTEXT.md`: project, repo, unassigned, weekly share. Never "workspace".
- End each commit message with a blank line and `Co-Authored-By: Claude Fable 5.2 <noreply@anthropic.com>`. Never push, never touch port 7777, never run `collect.py serve` or `launchctl`, never use `git stash`.

## The design in one place

**Tables** (all in `collector/components/claude_management/records.py`):

```python
SHARE = [
    "CREATE TABLE IF NOT EXISTS claude_management_share_plan (id INTEGER PRIMARY KEY CHECK (id = 1), budget REAL, "
    "start_day INTEGER NOT NULL, start_hour INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS claude_management_share_percents (project TEXT PRIMARY KEY, percent REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS claude_management_share_weeks (starts TEXT PRIMARY KEY, ends TEXT NOT NULL, budget REAL, "
    "closed INTEGER NOT NULL, updated TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS claude_management_share_use (week TEXT NOT NULL, project TEXT NOT NULL, cost REAL NOT NULL, "
    "percent REAL, PRIMARY KEY (week, project))",
    "CREATE TABLE IF NOT EXISTS claude_management_share_warnings (week TEXT NOT NULL, project TEXT NOT NULL, at TEXT NOT NULL, "
    "cost REAL NOT NULL, allowance REAL NOT NULL, PRIMARY KEY (week, project))",
]
```

- `share_plan` has at most one row. No row means: no budget, and the first week start, Thursday 21:00 UTC (`start_day` 3, Monday is 0; `start_hour` 21).
- `share_percents`: a project id (or `unassigned`) and its percent of the budget. No row means no share.
- `share_weeks`: one row for each week the collector saw. `starts` and `ends` are UTC, in the form `2026-11-05T21:00:00Z`. `closed` is 1 when the week has ended and was added up one last time. `budget` is the budget when the week was last added up.
- `share_use`: the cost of one project in one week (`week` is the `starts` of the week), with the percent the project had when the week was last added up. A project has a row when it has a cost above 0 or a percent.
- `share_warnings`: made once for a project and a week, when its cost reached its allowance. `at` is the time of the pass that made it.

**Words used below.** The *allowance* of a project is `budget × percent / 100`. A project is *over* when it has an allowance, its use is above 0, and its use is at or above the allowance. So a percent of 0 means "warn at the first cent", and no percent means "never warn".

**A week** is 7 days from each time the clock shows the week start. A week is named by its start. A moment exactly on the start belongs to the new week.

**The pass (reader three).** Given `now`, the setting and what Project management offers as `sessions`:

```
s, e = bounds(now, start_day, start_hour)          # e is s + 7 days
when Project management gave no session: change nothing, return
only after the week start was changed (never in normal running):
    a week row with starts > s          -> delete the row, its use rows and its warnings
    a week row with starts < s < ends   -> ends = s, closed = 0, delete its warnings
the row with starts == s                -> ends = e, closed = 0; make it when it is missing
for each week row with closed = 0, oldest first:
    use = for each project, the sum of the hourly costs with  starts[:13] <= hour < ends[:13]
          (a session Project management does not list counts as unassigned)
    replace the use rows of the week (cost rounded to 4 places, the percent of now); budget = the budget of now; updated = now
    for each project that is over: INSERT OR IGNORE a warning (at = now)
    when ends <= now: closed = 1
```

All of it in one transaction. A closed week is never touched again, so its numbers stay when Claude deletes old transcripts. A week with no pass at all (the service was off for more than a week) gets no row.

The hours come from `SELECT id, hours FROM claude_management_sessions WHERE last_ts >= ?` with the first 13 characters of the oldest open week's start. `hours` is JSON, `{"2026-11-06T09": 1.25}`: the first 13 characters of a UTC time and the cost of that hour.

**The answer of `GET /api/share`** (also the one record of the offer `share`):

```json
{"now": "2026-11-06T10:00:00Z",
 "week": {"start": "2026-11-05T21:00:00Z", "end": "2026-11-12T21:00:00Z"},
 "week_start": {"day": "thursday", "hour": 21},
 "budget": 200.0,
 "used": 12.5,
 "updated": "2026-11-06T10:00:00Z",
 "projects": [{"id": "parts", "name": "PartHub", "percent": 40.0, "allowance": 80.0, "use": 10.0, "left": 70.0,
               "over": false, "warned_at": null}],
 "unassigned": {"id": "unassigned", "name": "Unassigned", "percent": null, "allowance": null, "use": 2.5, "left": null,
                "over": false, "warned_at": null},
 "past": [{"start": "…", "end": "…", "budget": 200.0, "used": 31.0, "lines": [ …the same shape as a project line… ]}]}
```

- `week` is worked out from `now` and the setting, also before the first pass of a new week (then every `use` is 0 and `updated` is null).
- `week_start` is in UTC. `budget` is null when not set. `used` is the cost of every project and of unassigned in this week.
- `projects` has one line for each project Project management offers (its order; an id that comes twice is listed once). With Project management removed the list is empty.
- `unassigned` is null when it has no use row and no percent. It is also null when a config project has the id `unassigned` (then that project's line holds it).
- `allowance`, `left` are null without a budget or without a percent. `left` may be below 0.
- `past` holds at most 12 weeks that have ended, newest first. Its numbers come from the rows as they were frozen; a name comes from the projects of today, else the id is the name. Its lines are sorted by `use`, highest first.

## File Structure

| File | Responsibility |
|---|---|
| `collector/core/undo.py` (modify) | `restore` returns the folder it restored. |
| `collector/core/app.py` (modify) | `Core.restore`: runs `undo.restore` and writes the log line. One private helper writes a log line for both `run_action` and `restore`. |
| `collector/prune.py` (modify) | `restore` goes through `Core.restore`. |
| `collector/components/claude_management/records.py` (modify) | The five tables: `SHARE`, added to `RECORDS`; `CHANGES = [SHARE]`. |
| `collector/components/claude_management/share.py` (create) | The week (`utc_start`, `bounds`), the setting and percents readers, `update` (the pass), `view` (the answer). |
| `collector/components/claude_management/share_set.py` (create) | The three actions, the undo step, and the routes `/api/share…`. |
| `collector/components/claude_management/__init__.py` (modify) | Reader three, the new reads, actions, undo step, offer, and a `handle` that tries the share routes and then the settings routes. |
| `collector/share.py` (create) | A way in: the command line `show`, `budget`, `week-start`, `percent`. It holds no component code. |
| `dashboard/project.html`, `dashboard/home.html` (modify) | One line each, from `GET /api/share`. |
| `tests/test_share.py` (create) | Everything about the share, on a fake home with a fixed clock. |
| `tests/test_core.py`, `tests/test_components.py`, `tests/test_claude_management.py` (modify) | The restore log; the remove-one checks; the counts that changed (3 readers, 3 reads, the offers). |
| `CLAUDE.md`, `README.md`, `collector/components/README.md`, `CONTEXT.md` (modify) | Describe the share. |

## Review Focus

Inputs the spec does not name, most likely first. Each has its test in the task that owns the code.

1. **The store is down or locked.** The reader must do nothing and add no error to the pass (the snapshot is still written; `status.json` already shows `core_error`). `GET /api/share` answers 503 with a plain sentence, not a traceback. Tests: Task 3 and Task 4.
2. **Values that are not what they should be**, through the route or the command line: a budget of `"200"`, `true`, `-5`, `0`, `NaN`, infinity; a percent of 101 or `"40"`; a day `"someday"`; a time `"25:00"`; an offset `"+5"`. Each is refused with one plain sentence and nothing is written. Tests: Task 2.
3. **A session only one of the two components knows.** One that Project management does not list counts as unassigned. One that Project management lists and Claude management has no hours for adds nothing. Test: Task 3.
4. **The clock is exactly on the week start**, and **a pass after more than a week without one.** The exact moment is in the new week. After a long gap the old week is closed with its right numbers and no week is made for the gap. Tests: Task 2 (`bounds`) and Task 3.
5. **Two config projects with the same id, or one with the id `unassigned`.** The answer lists the id once and `unassigned` is null in the second case. Test: Task 4.

---

### Task 1: An applied restore is logged

Point 14. Today an applied `restore` leaves only the renamed manifest. After this task it also writes one line to `actions.jsonl`, like every other applied action.

**Files:**
- Modify: `collector/core/undo.py` (`restore`), `collector/core/app.py` (`Core`), `collector/prune.py` (`restore`)
- Test: `tests/test_core.py` (new class `RestoreLogTest`)

**Interfaces:**
- Produces: `core.undo.restore(...) -> Path` (the archive folder it worked on; everything else as before).
- Produces: `Core.restore(self, name: str, apply: bool, archive: Path, handlers: dict, on_applied=None, out=print) -> None`. It calls `undo.restore`. When `apply` is true it appends one line to `$STELLAXIS_HOME/actions.jsonl`, also when the restore stops with an error (then it writes the line and raises the same error):
  `{"at": now_iso(), "component": "core", "action": "restore", "args": {"name": <name as given>, "folder": <the folder's name, or null when none was found>}, "ok": bool, "manifest": <path of manifest.restored.json as text, or null when not ok>, "error": <"RestoreError: …" or null>}`.
  A dry run writes no line.

- [ ] **Step 1: Write the failing tests** in `tests/test_core.py`, class `RestoreLogTest`, on a temp home. Use a core with the real components folder and the settings save as the action to undo (see `SettingsActions` in `tests/test_claude_management.py` for a working save; use `test_hardening.fake_home`).

```python
def test_an_applied_restore_writes_one_log_line(self):
    r = self.save()                                   # one applied settings save: one line in actions.jsonl
    self.core.restore("last", True, self.archive, self.core.undo_handlers(self.cfg), out=lambda _: None)
    rows = self.log()
    self.assertEqual(len(rows), 2)
    row = rows[1]
    folder = Path(r["manifest"]).parent
    self.assertEqual((row["component"], row["action"], row["ok"], row["error"]), ("core", "restore", True, None))
    self.assertEqual(row["args"], {"name": "last", "folder": folder.name})
    self.assertEqual(row["manifest"], str(folder / "manifest.restored.json"))

def test_a_dry_run_restore_writes_no_log_line(self):
    self.save()
    self.core.restore("last", False, self.archive, self.core.undo_handlers(self.cfg), out=lambda _: None)
    self.assertEqual(len(self.log()), 1)

def test_a_restore_that_stops_is_logged_and_still_raises(self):
    r = self.save()
    Path(r["result"]["backup"]).unlink()
    with self.assertRaises(undo.RestoreError):
        self.core.restore("last", True, self.archive, self.core.undo_handlers(self.cfg), out=lambda _: None)
    row = self.log()[-1]
    self.assertEqual((row["action"], row["ok"], row["manifest"]), ("restore", False, None))
    self.assertIn("RestoreError", row["error"])

def test_prune_restore_goes_through_the_core(self):
    self.save()
    P.restore("last", True)                           # P is `import prune as P`
    self.assertEqual(self.log()[-1]["action"], "restore")
```

No test imports `prune` in process today, so take care: `prune.ARCHIVE` is worked out when the module is imported and would be the real `~/.claude/_archive`. In `setUp`, patch it (`mock.patch.object(P, "ARCHIVE", self.archive)`, with `self.archive = <fake claude dir> / "_archive"`). `prune.restore` also runs a collection with the config of the fake home, so `setUp` writes `<fake STELLAXIS_HOME>/config.json` with `{"scan_roots": [], "claude_bin": "/no/claude"}` and patches `live` and `app_records` of the command centre (as `FakeHome` in `tests/test_claude_management.py` does). Without these the test would read the real home.

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -X dev -m unittest tests.test_core -k RestoreLog -v`
Expected: FAIL, `Core` has no attribute `restore`.

- [ ] **Step 3: Implement.** `undo.restore` returns `d` at its end (both branches). In `core/app.py` move the writing of a log line into one private method (`_log(self, row: dict) -> None`) and use it in `run_action` and in the new `Core.restore`. In `prune.py`, `restore()` builds the handlers as today (with the `relocate` alias) and calls `C.the_core().restore(name, apply, ARCHIVE, handlers, on_applied=C.collect)`; a `RestoreError` still ends in `sys.exit(str(e))`. The printed output of `prune.py restore` must not change by one character (the reference test compares it).

- [ ] **Step 4: Run the tests**

Run: `python3 -X dev -m unittest tests.test_core tests.test_same_output tests.test_claude_management tests.test_relocate -v 2>&1 | tail -5`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add collector/core/undo.py collector/core/app.py collector/prune.py tests/test_core.py
git commit -m "An applied restore writes one line to actions.jsonl"
```

---

### Task 2: The tables, the week, and the three actions with their undo

**Files:**
- Modify: `collector/components/claude_management/records.py`, `collector/components/claude_management/__init__.py`
- Create: `collector/components/claude_management/share.py`, `collector/components/claude_management/share_set.py`
- Create: `tests/test_share.py`
- Modify: `tests/test_claude_management.py` (only the counts that changed)

**Interfaces:**
- Consumes: `Core.restore` (Task 1), for the undo tests.
- Produces, in `records.py`: `SHARE` (the five statements above, exactly), `RECORDS` = the two old statements plus `SHARE`, `CHANGES = [SHARE]`. The component gets `changes=records.CHANGES`.
- Produces, in `share.py`:
  - `DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")`
  - `FIRST_START = (3, 21)` with a comment: Thursday 21:00 UTC is Friday 02:30 in India, the owner's answer of 2026-11-12.
  - `UNASSIGNED = "unassigned"` with a comment: the id Project management gives the folders in no project.
  - `iso(t: datetime) -> str`: `2026-11-05T21:00:00Z`.
  - `utc_start(day: str, time: str, utc_offset: str) -> tuple[int, int]`: `("friday", "02:30", "+05:30")` gives `(3, 21)`. `day` is a full English day name or its first three letters, any case. `time` is `HH:MM`, 24 hours. `utc_offset` is `+HH:MM` or `-HH:MM`. It raises `ValueError` with one plain sentence when a part cannot be read. When the moment is not on a whole UTC hour it raises `ValueError` with exactly this form: `Friday 02:00 at +05:30 is Thursday 20:30 UTC. The week must start on a whole UTC hour, because the cost is kept for whole UTC hours. The nearest times that work are 01:30 and 02:30.`
  - `bounds(now: datetime, day: int, hour: int) -> tuple[datetime, datetime]`: the start of the week `now` is in (the last moment at or before `now` that is weekday `day`, `hour`:00 UTC) and that start plus 7 days. Both aware, UTC.
  - `setting(db) -> dict`: `{"budget": float | None, "day": int, "hour": int}`; with no row `{"budget": None, "day": 3, "hour": 21}`.
  - `percents(db) -> dict`: `{project id: percent}`.
  - `project_names(ctx) -> dict`: `{id: name}` of `ctx.read("project_management", "projects")`, in its order, an id listed once (the first wins), the name falling back to the id. `{}` when the read raises `RuntimeError` (Project management is there but did not load).
- Produces, in `share_set.py`:
  - `set_budget(plan, ctx, budget) -> bool`: `budget` is a number above 0, or `None` to remove it.
  - `set_week_start(plan, ctx, day, time, utc_offset=None) -> bool`: `utc_offset=None` means the Mac's own offset at this moment (`datetime.now().astimezone().utcoffset()`).
  - `set_percent(plan, ctx, project, percent) -> bool`: `percent` is a number from 0 to 100, or `None` to remove the share. `project` must be an id of `project_names(ctx)` or `unassigned`.
  - `undo_setting(entry: dict, apply: bool, out, ctx) -> bool`, the handler of the step `claude_management.share_setting`.
- Produces, in `__init__.py`: `reads` gets `("project_management", "sessions")` and `("project_management", "projects")`; `actions` gets `set_budget`, `set_week_start`, `set_percent`; `undo` gets `claude_management.share_setting`.

**What each action says and writes.** Each one reads the present value, says one line with `plan.say`, and with `plan.apply` writes the store and adds one manifest entry. Money is written as `$200.00` (`f"${x:,.2f}"`), a percent as `40%` (no trailing `.0`; `12.5%` stays).

| Action | The line (`plan.say`) | Manifest entry |
|---|---|---|
| `set_budget` | `weekly budget: not set becomes $200.00` · `weekly budget: $200.00 becomes $250.00` · `weekly budget: $250.00 becomes not set` | `{"op": "claude_management.share_setting", "what": "budget", "prev": <number or null>, "new": <number or null>}` |
| `set_week_start` | `week start: Thursday 21:00 UTC becomes Sunday 18:00 UTC (Sunday 23:30 at +05:30)` | `{"op": …, "what": "week_start", "prev": [3, 21], "new": [6, 18]}` |
| `set_percent` | `PartHub (parts): no share becomes 40% of the weekly budget` · `PartHub (parts): 40% becomes 25% of the weekly budget` · `PartHub (parts): 40% becomes no share` | `{"op": …, "what": "percent", "project": "parts", "prev": <number or null>, "new": <number or null>}` |

- When the new value equals the present one the line is `weekly budget: stays $200.00` (and the like), nothing is written, no manifest entry is added, and the action returns True.
- `set_week_start` also notes (`plan.note`): `The weeks are added up again at the next collection.`
- `set_percent` also notes, when the percents then add up to more than 100: `The shares now add up to 140%. That is allowed.`
- `plan.result` is `{"what": …, "prev": …, "new": …}` (and `"project"` for a percent).
- **A refusal** is `plan.note(<one plain sentence>)` and `return False`; nothing is written. The sentences:
  - budget: `The weekly budget must be a number above 0, or none to remove it.` (also for a text, a bool, NaN, infinity)
  - week start: the text of the `ValueError` of `utc_start`.
  - percent: `The percent must be a number from 0 to 100, or none to remove the share.`
  - project: `No project has the id 'x'. The ids are: parts, curl, unassigned.`
- Removing the budget keeps the week start: the row stays with `budget` NULL.
- **The undo** puts `prev` back and says one line: `weekly budget: $250.00 goes back to $200.00`. When the present value is not the entry's `new` (it was changed again since), it still puts `prev` back and the line ends with ` (it was changed again after this action)`. With `apply` false it says the line with the dry-run prefix (`core.undo.pre`) and writes nothing. It returns True.

- [ ] **Step 1: Write the test scaffolding and the failing tests** in `tests/test_share.py`.

The base class, used by every later task:

```python
import prune as P
import test_claude_management as TCM        # its FakeHome has no tests of its own
C, COMPS, CENTRE = TCM.C, TCM.COMPS, TCM.CENTRE
PRICE = {"claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.2}}     # 250,000 input tokens cost $1.00

class ShareHome(TCM.FakeHome):
    """The fake home plus two projects (PartHub: code/parts, Curlberry: code/curl), a folder in no project (code/loose),
    no session at the start, and a clock the test sets."""
    def setUp(self):
        super().setUp()
        self.transcript.unlink()
        code = self.app.parent
        self.parts, self.curl, self.loose = code / "parts", code / "curl", code / "loose"
        for d in (self.parts, self.curl, self.loose):
            d.mkdir()
        self.cfg = {**TCM.CFG, "pricing": PRICE, "projects": [
            {"id": "parts", "name": "PartHub", "repos": [str(self.parts)]},
            {"id": "curl", "name": "Curlberry", "repos": [str(self.curl)]}]}
        (self.stx / "config.json").write_text(json.dumps(self.cfg))     # the routes and the command line read the config from here
        patch = mock.patch.object(P, "ARCHIVE", self.claude / "_archive")   # prune worked its archive folder out at import
        patch.start()
        self.addCleanup(patch.stop)
        self.n = 0

    def at(self, when):                                  # the clock, until the test ends or `at` is called again
        os.environ["STELLAXIS_NOW"] = when
        self.addCleanup(os.environ.pop, "STELLAXIS_NOW", None)

    def turn(self, session, folder, ts, dollars, file=None):
        """One assistant turn of `session`, run in `folder` at `ts`, that costs `dollars`."""
        self.n += 1
        f = self.claude / "projects" / (file or "p") / f"{session}.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "a") as out:
            out.write(json.dumps({"type": "assistant", "timestamp": ts, "sessionId": session, "cwd": str(folder), "gitBranch": "main",
                                  "message": {"id": f"msg_{self.n:016d}", "model": "claude-opus-5-5",
                                              "usage": {"input_tokens": int(dollars * 250000), "output_tokens": 0}}}) + "\n")

    def act(self, action, apply=True, **args):
        return C.the_core().run_action("claude_management", action, args, apply, self.cfg)

    def sql(self, query, *args):
        with closing(sqlite3.connect(self.stx / "stellaxis.db")) as db:
            return db.execute(query, args).fetchall()

    def log(self):
        f = self.stx / "actions.jsonl"
        return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []
```

(`os.environ.pop` in `addCleanup` may be registered more than once; that is fine. The message id must be unique in its last 16 characters: the transcript helper drops a repeated id.)

The tests of this task (names and what each asserts):

```python
class WeekTest(unittest.TestCase):
    def test_friday_0230_in_india_is_thursday_2100_utc(self):
        self.assertEqual(SHARE.utc_start("friday", "02:30", "+05:30"), (3, 21))
        self.assertEqual(SHARE.utc_start("Fri", "02:30", "+05:30"), (3, 21))
        self.assertEqual(SHARE.utc_start("monday", "00:00", "+00:00"), (0, 0))
        self.assertEqual(SHARE.utc_start("monday", "01:00", "+05:00"), (6, 20))      # goes back over the start of the list
        self.assertEqual(SHARE.utc_start("sunday", "20:00", "-08:00"), (0, 4))       # goes on over the end of the list

    def test_a_start_that_is_not_on_a_whole_utc_hour_is_refused_in_plain_words(self):
        with self.assertRaises(ValueError) as e:
            SHARE.utc_start("friday", "02:00", "+05:30")
        self.assertEqual(str(e.exception), "Friday 02:00 at +05:30 is Thursday 20:30 UTC. The week must start on a whole UTC hour, "
                         "because the cost is kept for whole UTC hours. The nearest times that work are 01:30 and 02:30.")

    def test_a_part_that_cannot_be_read_is_refused(self):
        for day, time, off in (("someday", "02:30", "+05:30"), ("friday", "25:00", "+05:30"), ("friday", "2.30", "+05:30"),
                               ("friday", "02:30", "+5"), ("friday", "02:30", "IST")):
            with self.assertRaises(ValueError, msg=(day, time, off)):
                SHARE.utc_start(day, time, off)

    def test_the_week_of_a_moment(self):
        utc = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        self.assertEqual(SHARE.bounds(utc("2026-11-06T10:00:00Z"), 3, 21), (utc("2026-11-05T21:00:00Z"), utc("2026-11-12T21:00:00Z")))
        self.assertEqual(SHARE.bounds(utc("2026-11-05T21:00:00Z"), 3, 21)[0], utc("2026-11-05T21:00:00Z"))   # exactly on the start: the new week
        self.assertEqual(SHARE.bounds(utc("2026-11-05T20:59:59Z"), 3, 21)[0], utc("2026-10-29T21:00:00Z"))
```

`SHARE` is `import_folder(COMPS / "claude_management").share`; `SET` is `.share_set`.

```python
class TablesTest(ShareHome):
    def test_a_store_from_before_this_step_gets_the_tables_through_one_change(self):
        # make the store as step 3 left it: the two old tables, and no share table
        with closing(sqlite3.connect(self.stx / "stellaxis.db")) as db, db:
            for sql in CM.records.RECORDS[:2]:
                db.execute(sql)
            db.execute("CREATE TABLE core_schema (component TEXT PRIMARY KEY, version INTEGER NOT NULL)")
            db.execute("INSERT INTO core_schema VALUES ('claude_management', 0)")
        core = Core(self.stx, self.claude, COMPS)
        self.assertIsNone([c for c in core.components if c.name == "claude_management"][0].error)
        self.assertEqual(self.sql("SELECT version FROM core_schema WHERE component = 'claude_management'"), [(1,)])
        names = {r[0] for r in self.sql("SELECT name FROM sqlite_master WHERE name LIKE 'claude_management_share_%'")}
        self.assertEqual(names, {"claude_management_share_" + n for n in ("plan", "percents", "weeks", "use", "warnings")})

    def test_with_nothing_set_there_is_no_budget_and_the_week_starts_thursday_2100_utc(self):
        C.the_core()
        with closing(C.the_core().ctx("claude_management", self.cfg).db()) as db:
            self.assertEqual(SHARE.setting(db), {"budget": None, "day": 3, "hour": 21})
            self.assertEqual(SHARE.percents(db), {})


class ActionsTest(ShareHome):
    def setting(self): ...        # SHARE.setting on a fresh connection
    def percents(self): ...

    def test_a_dry_run_says_what_it_would_do_and_changes_nothing(self):
        r = self.act("set_budget", apply=False, budget=200)
        self.assertEqual((r["ok"], r["steps"], r["manifest"]), (True, ["weekly budget: not set becomes $200.00"], None))
        self.assertEqual(self.setting()["budget"], None)
        self.assertEqual(self.log(), [])
        self.assertFalse((self.claude / "_archive").exists())

    def test_an_applied_budget_is_kept_logged_and_can_be_undone(self):
        self.act("set_budget", budget=200)
        r = self.act("set_budget", budget=250.5)
        self.assertEqual(r["steps"], ["weekly budget: $200.00 becomes $250.50"])
        self.assertEqual(self.setting()["budget"], 250.5)
        self.assertEqual([(row["action"], row["ok"], row["args"]) for row in self.log()],
                         [("set_budget", True, {"budget": 200}), ("set_budget", True, {"budget": 250.5})])
        said = []
        self.restore(r["manifest"], out=said.append)             # through Core.restore, as TCM.SettingsActions.restore names the folder
        self.assertEqual(said[-1].strip(), "weekly budget: $250.50 goes back to $200.00")
        self.assertEqual(self.setting()["budget"], 200)
        self.assertEqual(self.log()[-1]["action"], "restore")

    def test_removing_the_budget_keeps_the_week_start(self):
        self.act("set_week_start", day="monday", time="05:30", utc_offset="+05:30")
        self.act("set_budget", budget=200)
        r = self.act("set_budget", budget=None)
        self.assertEqual(r["steps"], ["weekly budget: $200.00 becomes not set"])
        self.assertEqual(self.setting(), {"budget": None, "day": 0, "hour": 0})

    def test_the_same_value_again_writes_nothing(self):
        self.act("set_budget", budget=200)
        r = self.act("set_budget", budget=200)
        self.assertEqual((r["ok"], r["steps"], r["manifest"]), (True, ["weekly budget: stays $200.00"], None))

    def test_a_budget_that_is_not_a_number_above_0_is_refused(self):
        for bad in ("200", True, -5, 0, float("nan"), float("inf")):
            r = self.act("set_budget", budget=bad)
            self.assertEqual((r["ok"], r["notes"]), (False, ["The weekly budget must be a number above 0, or none to remove it."]), bad)
        self.assertEqual(self.setting()["budget"], None)
        self.assertFalse((self.claude / "_archive").exists())

    def test_the_week_start_is_set_in_local_time_and_kept_in_utc(self):
        r = self.act("set_week_start", day="sunday", time="23:30", utc_offset="+05:30")
        self.assertEqual(r["steps"], ["week start: Thursday 21:00 UTC becomes Sunday 18:00 UTC (Sunday 23:30 at +05:30)"])
        self.assertEqual(r["notes"], ["The weeks are added up again at the next collection."])
        self.assertEqual((self.setting()["day"], self.setting()["hour"]), (6, 18))
        self.restore(r["manifest"])
        self.assertEqual((self.setting()["day"], self.setting()["hour"]), (3, 21))

    def test_a_week_start_off_the_whole_utc_hour_is_refused_and_says_what_works(self):
        r = self.act("set_week_start", day="friday", time="02:00", utc_offset="+05:30")
        self.assertFalse(r["ok"])
        self.assertIn("The nearest times that work are 01:30 and 02:30.", r["notes"][0])
        self.assertEqual((self.setting()["day"], self.setting()["hour"]), (3, 21))

    def test_without_an_offset_the_macs_own_is_used(self):
        # patch the one place share_set asks the Mac for its offset so it gives +05:30; then friday 02:30 gives (3, 21) "stays"
        ...

    def test_a_percent_is_set_changed_and_removed(self):
        r = self.act("set_percent", project="parts", percent=40)
        self.assertEqual(r["steps"], ["PartHub (parts): no share becomes 40% of the weekly budget"])
        r2 = self.act("set_percent", project="parts", percent=12.5)
        self.assertEqual(r2["steps"], ["PartHub (parts): 40% becomes 12.5% of the weekly budget"])
        r3 = self.act("set_percent", project="parts", percent=None)
        self.assertEqual(r3["steps"], ["PartHub (parts): 12.5% becomes no share"])
        self.assertEqual(self.percents(), {})
        self.restore(r3["manifest"])
        self.assertEqual(self.percents(), {"parts": 12.5})

    def test_a_percent_of_0_is_a_share_and_is_not_the_same_as_none(self):
        self.act("set_percent", project="parts", percent=0)
        self.assertEqual(self.percents(), {"parts": 0})

    def test_shares_may_add_up_to_more_than_100_and_the_action_says_so(self):
        self.act("set_percent", project="parts", percent=80)
        r = self.act("set_percent", project="curl", percent=60)
        self.assertEqual((r["ok"], r["notes"]), (True, ["The shares now add up to 140%. That is allowed."]))

    def test_unassigned_can_be_given_a_share(self):
        r = self.act("set_percent", project="unassigned", percent=10)
        self.assertEqual(r["steps"], ["Unassigned (unassigned): no share becomes 10% of the weekly budget"])

    def test_a_percent_outside_0_to_100_or_an_unknown_project_is_refused(self):
        for bad in (101, -1, "40", True, float("nan")):
            r = self.act("set_percent", project="parts", percent=bad)
            self.assertEqual((r["ok"], r["notes"]), (False, ["The percent must be a number from 0 to 100, or none to remove the share."]), bad)
        r = self.act("set_percent", project="nope", percent=10)
        self.assertEqual(r["notes"], ["No project has the id 'nope'. The ids are: parts, curl, unassigned."])
        self.assertEqual(self.percents(), {})

    def test_an_undo_after_a_later_change_says_so_and_still_puts_the_old_value_back(self):
        first = self.act("set_budget", budget=200)
        self.act("set_budget", budget=300)
        said = []
        self.restore(first["manifest"], out=said.append)
        self.assertEqual(said[-1].strip(), "weekly budget: $300.00 goes back to not set (it was changed again after this action)")
        self.assertEqual(self.setting()["budget"], None)

    def test_a_dry_run_of_the_undo_changes_nothing(self):
        r = self.act("set_budget", budget=200)
        said = []
        self.restore(r["manifest"], out=said.append, apply=False)
        self.assertIn("[dry-run] weekly budget: $200.00 goes back to not set", said[1])
        self.assertEqual(self.setting()["budget"], 200)
```

`self.restore(manifest, out=lambda _: None, apply=True)` calls `C.the_core().restore(Path(manifest).parent.name, apply, self.claude / "_archive", C.the_core().undo_handlers(self.cfg), out=out)`.

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -X dev -m unittest tests.test_share -v 2>&1 | tail -5`
Expected: FAIL (no module attribute `share`).

- [ ] **Step 3: Implement** `records.py` (`SHARE`, `RECORDS`, `CHANGES`), `share.py` (the names of this task's Interfaces only; `update` and `view` come in Tasks 3 and 4), `share_set.py` (the three actions and `undo_setting`), and wire them in `__init__.py`. The actions and the undo open their own connection (`ctx.db()`), close it, and write in one transaction.

- [ ] **Step 4: Update the counts in `tests/test_claude_management.py`** that this task changes (`test_the_component_loads` asserts the reads) and run:

Run: `python3 -X dev -m unittest tests.test_share tests.test_claude_management tests.test_components tests.test_core tests.test_same_output 2>&1 | tail -5`
Expected: OK, no warning.

- [ ] **Step 5: Commit**

```bash
git add collector/components/claude_management tests/test_share.py tests/test_claude_management.py
git commit -m "Weekly share: the tables, the week, and the actions that set the budget, the week start and a percent"
```

---

### Task 3: Reader three adds up each week and makes the warnings

**Files:**
- Modify: `collector/components/claude_management/share.py`, `collector/components/claude_management/__init__.py`
- Modify: `tests/test_share.py`, `tests/test_claude_management.py` (the reader count)

**Interfaces:**
- Consumes: `bounds`, `setting`, `percents`, `iso`, `UNASSIGNED` (Task 2); `ctx.read("project_management", "sessions")` gives `[{"id", "project", "repo", "branch", "first_ts", "last_ts"}]` where `project` is a project id or `unassigned`.
- Produces: `share.update(ctx, placed: list, now: datetime) -> None`: the pass, exactly as written under "The pass (reader three)" above. `placed` is the list Project management offers. It returns without a change when `placed` is empty, and without an error when the store is down (`core.app.StoreError`).
- Produces: `share.over(use: float, allowance: float | None) -> bool`: True when `allowance` is not None, `use > 0` and `use >= allowance`.
- Produces: in `__init__.py`, `share_reader(ctx)`, the third reader:

```python
def share_reader(ctx) -> None:
    """Round three: the cost of each project in the week that is running, from the hourly costs of round one and the
    sessions Project management wrote in round two. When round one failed, the last good numbers stay."""
    if "collector" not in ctx.inputs or "files" not in ctx.memo:
        return
    share.update(ctx, ctx.read("project_management", "sessions"), now())
```

- [ ] **Step 1: Write the failing tests** in `tests/test_share.py`, class `ReaderTest(ShareHome)`. Helpers: `self.collect()` is `C.collect(dict(self.cfg))`; `self.use(week)` is `{project: cost}` of the rows of that week; `self.weeks()` is `[(starts, ends, closed)]` ordered by `starts`; `self.warnings()` is `[(week, project, at)]` ordered by `week`, `project`. `W1 = "2026-11-05T21:00:00Z"`, `W2 = "2026-11-12T21:00:00Z"`, `W0 = "2026-10-29T21:00:00Z"`.

```python
def test_each_project_gets_the_cost_of_its_sessions_in_this_week(self):
    self.turn("a", self.parts, "2026-11-06T09:10:00Z", 3)
    self.turn("b", self.curl, "2026-11-06T09:20:00Z", 2)
    self.turn("c", self.loose, "2026-11-06T09:30:00Z", 1)
    self.at("2026-11-06T10:00:00Z")
    self.collect()
    self.assertEqual(self.weeks(), [(W1, W2, 0)])
    self.assertEqual(self.use(W1), {"parts": 3.0, "curl": 2.0, "unassigned": 1.0})
    self.assertEqual(self.warnings(), [])                       # no budget: no warning

def test_a_week_boundary_in_the_middle_of_a_session_splits_its_cost(self):
    self.turn("a", self.parts, "2026-11-05T20:59:00Z", 3)       # one minute before the week starts: the week before
    self.turn("a", self.parts, "2026-11-05T21:00:00Z", 2)       # exactly on the start: this week
    self.at("2026-11-06T10:00:00Z")
    self.collect()
    self.assertEqual(self.use(W1), {"parts": 2.0})
    self.assertEqual(self.weeks(), [(W1, W2, 0)])               # the week before had no pass: it gets no row

def test_a_session_with_files_in_two_projects_repos_counts_once(self):
    self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3, file="p")
    self.turn("a", self.curl, "2026-11-06T10:00:00Z", 2, file="q")      # the same session, resumed in a repo of another project
    self.at("2026-11-06T11:00:00Z")
    self.collect()
    use = self.use(W1)
    self.assertEqual(sum(use.values()), 5.0)
    self.assertEqual(len(use), 1)                               # all of it in one project

def test_a_repo_that_moves_to_another_project_takes_its_weeks_cost_along(self):
    self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)
    self.at("2026-11-06T10:00:00Z")
    self.collect()
    self.cfg["projects"][0]["repos"], self.cfg["projects"][1]["repos"] = [], [str(self.curl), str(self.parts)]
    self.at("2026-11-06T10:05:00Z")
    self.collect()
    self.assertEqual(self.use(W1), {"curl": 3.0})

def test_a_new_week_starts_at_zero_and_the_week_before_is_kept(self):
    self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)
    self.at("2026-11-06T10:00:00Z")
    self.collect()
    self.turn("a", self.parts, "2026-11-12T20:30:00Z", 1)       # still week 1, after its last pass
    self.turn("a", self.parts, "2026-11-12T21:30:00Z", 2)       # week 2
    self.at("2026-11-12T22:00:00Z")
    self.collect()
    self.assertEqual(self.weeks(), [(W1, W2, 1), (W2, "2026-11-19T21:00:00Z", 0)])
    self.assertEqual(self.use(W1), {"parts": 4.0})              # added up one last time, with the late turn
    self.assertEqual(self.use(W2), {"parts": 2.0})

def test_a_closed_week_keeps_its_numbers_when_the_transcript_is_gone(self):
    self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)
    self.at("2026-11-06T10:00:00Z")
    self.collect()
    self.at("2026-11-13T10:00:00Z")
    self.collect()                                              # closes week 1
    (self.claude / "projects" / "p" / "a.jsonl").unlink()
    self.turn("b", self.curl, "2026-11-13T11:00:00Z", 1)
    self.at("2026-11-13T12:00:00Z")
    self.collect()
    self.assertEqual(self.use(W1), {"parts": 3.0})

def test_a_pass_after_a_long_gap_closes_the_old_week_and_makes_no_week_for_the_gap(self):
    self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)
    self.at("2026-11-06T10:00:00Z")
    self.collect()
    self.at("2026-11-27T10:00:00Z")                             # three weeks later
    self.collect()
    self.assertEqual(self.weeks(), [(W1, W2, 1), ("2026-11-26T21:00:00Z", "2026-12-03T21:00:00Z", 0)])
    self.assertEqual(self.use(W1), {"parts": 3.0})

def test_a_project_that_uses_all_of_its_share_is_warned_once(self):
    self.act("set_budget", budget=10)
    self.act("set_percent", project="parts", percent=40)        # an allowance of $4.00
    self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)
    self.at("2026-11-06T10:00:00Z")
    self.collect()
    self.assertEqual(self.warnings(), [])                       # $3.00 of $4.00
    self.turn("a", self.parts, "2026-11-06T10:30:00Z", 1)
    self.at("2026-11-06T11:00:00Z")
    self.collect()
    self.assertEqual(self.warnings(), [(W1, "parts", "2026-11-06T11:00:00Z")])      # $4.00 of $4.00: all of it
    self.turn("a", self.parts, "2026-11-06T11:30:00Z", 5)
    self.at("2026-11-06T12:00:00Z")
    self.collect()
    self.assertEqual(self.warnings(), [(W1, "parts", "2026-11-06T11:00:00Z")])      # not again, and the first time stays
    self.assertEqual(self.sql("SELECT cost, allowance FROM claude_management_share_warnings"), [(4.0, 4.0)])

def test_a_new_week_can_warn_again_and_the_old_warning_stays(self):
    # as above until warned in W1; then a turn of $4 in W2 and a pass in W2
    ...
    self.assertEqual([(w, p) for w, p, _ in self.warnings()], [(W1, "parts"), (W2, "parts")])

def test_a_percent_of_0_warns_at_the_first_cent_and_no_percent_never_warns(self):
    self.act("set_budget", budget=10)
    self.act("set_percent", project="parts", percent=0)
    self.turn("a", self.parts, "2026-11-06T09:00:00Z", 1)
    self.turn("b", self.curl, "2026-11-06T09:00:00Z", 9)        # Curlberry has no percent
    self.at("2026-11-06T10:00:00Z")
    self.collect()
    self.assertEqual([(w, p) for w, p, _ in self.warnings()], [(W1, "parts")])
    self.assertEqual(self.sql("SELECT project, percent FROM claude_management_share_use ORDER BY project"), [("curl", None), ("parts", 0.0)])

def test_a_share_of_0_with_no_use_does_not_warn_and_still_has_a_row(self):
    self.act("set_budget", budget=10)
    self.act("set_percent", project="parts", percent=0)
    self.turn("b", self.curl, "2026-11-06T09:00:00Z", 1)
    self.at("2026-11-06T10:00:00Z")
    self.collect()
    self.assertEqual(self.warnings(), [])
    self.assertEqual(self.use(W1), {"curl": 1.0, "parts": 0.0})

def test_shares_that_add_up_to_more_than_100_each_count_on_their_own(self):
    # budget 10, parts 80, curl 60; parts uses 8, curl uses 5 -> only parts is warned
    ...

def test_without_a_budget_nothing_warns(self):
    # parts has 40 percent, no budget, parts uses 100 -> no warning, the use row is there with percent 40
    ...

def test_unassigned_warns_only_when_it_was_given_a_share(self):
    # budget 10; loose uses 5 -> no warning; then set_percent unassigned 10, next pass -> warned
    ...

def test_the_week_start_changed_in_the_middle_of_a_week_cuts_the_running_week_and_counts_no_hour_twice(self):
    self.turn("a", self.parts, "2026-11-06T09:00:00Z", 3)       # Friday
    self.turn("a", self.parts, "2026-11-09T05:00:00Z", 2)       # Monday morning
    self.at("2026-11-09T10:00:00Z")
    self.collect()
    self.assertEqual(self.use(W1), {"parts": 5.0})
    self.act("set_week_start", day="monday", time="00:00", utc_offset="+00:00")
    self.at("2026-11-09T10:05:00Z")
    self.collect()
    self.assertEqual(self.weeks(), [(W1, "2026-11-09T00:00:00Z", 1), ("2026-11-09T00:00:00Z", "2026-11-16T00:00:00Z", 0)])
    self.assertEqual(self.use(W1), {"parts": 3.0})
    self.assertEqual(self.use("2026-11-09T00:00:00Z"), {"parts": 2.0})

def test_a_week_start_moved_back_before_the_running_week_takes_the_running_week_in(self):
    self.turn("a", self.parts, "2026-11-03T09:00:00Z", 1)       # Tuesday, week 0
    self.at("2026-11-03T10:00:00Z")
    self.collect()
    self.turn("a", self.parts, "2026-11-04T09:00:00Z", 2)       # Wednesday, week 0
    self.turn("a", self.parts, "2026-11-06T09:00:00Z", 4)       # Friday, week 1
    self.at("2026-11-06T10:00:00Z")
    self.collect()
    self.assertEqual((self.use(W0), self.use(W1)), ({"parts": 3.0}, {"parts": 4.0}))
    self.act("set_week_start", day="wednesday", time="00:00", utc_offset="+00:00")
    self.at("2026-11-06T10:05:00Z")
    self.collect()
    new = "2026-11-04T00:00:00Z"
    self.assertEqual(self.weeks(), [(W0, new, 1), (new, "2026-11-11T00:00:00Z", 0)])
    self.assertEqual((self.use(W0), self.use(new)), ({"parts": 1.0}, {"parts": 6.0}))

def test_a_warning_of_a_week_that_is_cut_is_made_again_only_when_it_is_still_true(self):
    # budget 10, parts 40 (allowance 4); 3 on Friday and 2 on Monday -> warned in W1 at the first pass;
    # week start to Monday 00:00 UTC; next pass: W1 now holds 3 (not over): its warning is gone;
    # the new week holds 2 (not over): no warning at all
    ...

def test_a_session_project_management_does_not_list_counts_as_unassigned(self):
    # call SHARE.update directly with placed = [{"id": "a", "project": "parts"}] after a collection that holds a and b
    ...
    self.assertEqual(self.use(W1), {"parts": 3.0, "unassigned": 2.0})

def test_a_listed_session_with_no_hours_adds_nothing(self):
    # placed also lists a session id the store does not hold: no row, no error
    ...

def test_when_project_management_gives_no_session_nothing_changes(self):
    # one good pass; then patch the offer "sessions" of project_management to return [] (mock.patch.dict on its offers);
    # a second pass at a later clock leaves weeks, use and warnings byte for byte as they were
    ...

def test_with_the_store_down_the_pass_has_no_error_of_the_share(self):
    # as TCM.test_with_the_store_down_the_snapshot_is_still_written makes the store fail:
    # the snapshot is written and status.json's index_error is None
    ...

def test_a_pass_that_is_not_a_collection_adds_nothing_up(self):
    C.the_core().run_pass(self.cfg, {})
    self.assertEqual(self.weeks(), [])
```

Every `...` test is written out in full by the implementer, with the numbers its comment gives.

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -X dev -m unittest tests.test_share -k ReaderTest 2>&1 | tail -5`
Expected: FAIL (no rows in `claude_management_share_weeks`).

- [ ] **Step 3: Implement** `share.update` and `share.over`, and add `share_reader` as the third reader in `__init__.py` (`readers=[usage_reader, setup_reader, share_reader]`). Sum the hourly costs unrounded and round each project's total to 4 places when it is written.

- [ ] **Step 4: Update the reader count** in `tests/test_claude_management.py` (`len(c.readers)` is 3) and run:

Run: `python3 -X dev -m unittest tests.test_share tests.test_claude_management tests.test_components tests.test_same_output 2>&1 | tail -5`
Expected: OK, no warning.

- [ ] **Step 5: Commit**

```bash
git add collector/components/claude_management tests/test_share.py tests/test_claude_management.py
git commit -m "Weekly share: reader three adds up each project's week and makes one warning when a share is used up"
```

---

### Task 4: The route, the offer and the command line

**Files:**
- Modify: `collector/components/claude_management/share.py` (`view`), `share_set.py` (`handle`), `__init__.py` (offer, handle)
- Create: `collector/share.py`
- Modify: `tests/test_share.py`, `tests/test_claude_management.py` (the offers), `tests/test_components.py` (remove-one)

**Interfaces:**
- Consumes: everything of Tasks 2 and 3.
- Produces: `share.view(ctx, names: dict, now: datetime) -> dict`: the answer described under "The answer of `GET /api/share`". `names` is `project_names(ctx)`. It reads the store only; it raises `StoreError` when the store is down.
- Produces: the offer `share` of Claude management: `lambda ctx: [share.view(ctx, share.project_names(ctx), now())]` (a list with one record, so a way in reads it with `Core.read`).
- Produces: `share_set.handle(handler, method, ctx) -> bool`, False when the path does not start with `/api/share`:
  - `GET /api/share`: 200 and the view. 503 `{"error": "The store is down: …"}` on `StoreError`.
  - `POST /api/share/budget` with `{"budget": <number or null>, "apply": <bool>}`
  - `POST /api/share/week-start` with `{"day": "friday", "time": "02:30", "utc_offset": "+05:30", "apply": <bool>}` (`utc_offset` may be left out)
  - `POST /api/share/percent` with `{"project": "parts", "percent": <number or null>, "apply": <bool>}`
  - Each POST runs its action through `ctx.act(action, args, apply)` and answers 200 with the dict `ctx.act` returns. When the action refused (`ok` false) it answers 400 with `{"error": <the notes joined by a space>, **that dict}`.
  - `"apply"` must be `true` or `false`, else 400 `"apply" must be true or false`. A missing `budget`, `day`, `time`, `project` or `percent` key is 400 with a sentence that names the key.
  - Not from this host: 403 (as `settings.handle` does). Any other path under `/api/share`: 404 `{"error": "no such share endpoint"}`.
- Produces: in `__init__.py`, `handle(handler, method, ctx)` = `share_set.handle(...) or settings.handle(...)`.
- Produces: `collector/share.py`, a way in:

```
python3 share.py show
python3 share.py budget <amount|none> [--apply]
python3 share.py week-start <day> <HH:MM> [--utc-offset +05:30] [--apply]
python3 share.py percent <project id> <percent|none> [--apply]
```

  It imports `collect as C` and `core.undo`, as `prune.py` does. Each setting command runs its action with `C.the_core().run_action("claude_management", action, args, apply, C.load_config(), plan=undo.Plan(apply, "share-" + <command>, C.CLAUDE_DIR / "_archive", undo_hint=<the prune.py restore line, as prune.py builds it>))`, so the dry run, the manifest path and the undo line print as they do for `prune.py`. A refusal prints its sentence (the plan's `note` prints it) and exits 1. With Claude management not loaded every command exits 1 with the one line `Claude management is not loaded`. The module docstring is the usage text above plus one line on undo (`python3 prune.py restore <stamp> --apply`).
  `show` prints the view as text. Its lines, with the numbers of the example answer above:

```
Week: 2026-11-05 21:00 UTC to 2026-11-12 21:00 UTC (a new week starts every Thursday 21:00 UTC)
Budget: $200.00 a week. Used so far: $12.50.
PartHub (parts): $10.00 used of $80.00 (40% of the budget), $70.00 left
Unassigned (unassigned): $2.50 used, no share
```

  With no budget the second line is `Budget: not set. Used so far: $12.50.` and a project with a percent reads `PartHub (parts): $10.00 used, 40% of a budget that is not set`. A line of a project that is over ends with `. All of its share is used.` After the lines of this week it prints `Past weeks:` and one line for each past week, `2026-10-29 21:00 UTC to 2026-11-05 21:00 UTC: $31.00 used of $200.00` (without `of …` when that week had no budget); it prints nothing for past weeks when there are none. When the offer gives nothing (the store is down) it exits 1 with `The share cannot be read: the store is down`.

- [ ] **Step 1: Write the failing tests** in `tests/test_share.py`.

`ViewTest(ShareHome)` reads the view with `self.share()` = `C.the_core().read("claude_management", "share", self.cfg)[0]`:

```python
def test_the_answer_for_a_week_with_a_budget(self):
    self.act("set_budget", budget=200)
    self.act("set_percent", project="parts", percent=40)
    self.turn("a", self.parts, "2026-11-06T09:00:00Z", 10)
    self.turn("c", self.loose, "2026-11-06T09:00:00Z", 2.5)
    self.at("2026-11-06T10:00:00Z")
    self.collect()
    v = self.share()
    self.assertEqual({k: v[k] for k in ("now", "week", "week_start", "budget", "used", "updated")},
                     {"now": "2026-11-06T10:00:00Z", "week": {"start": W1, "end": W2}, "week_start": {"day": "thursday", "hour": 21},
                      "budget": 200.0, "used": 12.5, "updated": "2026-11-06T10:00:00Z"})
    self.assertEqual(v["projects"], [
        {"id": "parts", "name": "PartHub", "percent": 40.0, "allowance": 80.0, "use": 10.0, "left": 70.0, "over": False, "warned_at": None},
        {"id": "curl", "name": "Curlberry", "percent": None, "allowance": None, "use": 0, "left": None, "over": False, "warned_at": None}])
    self.assertEqual(v["unassigned"], {"id": "unassigned", "name": "Unassigned", "percent": None, "allowance": None, "use": 2.5,
                                       "left": None, "over": False, "warned_at": None})
    self.assertEqual(v["past"], [])

def test_a_project_over_its_share_is_marked_and_carries_the_time_of_its_warning(self):
    # budget 10, parts 40, parts uses 5 at a pass at 2026-11-06T10:00:00Z
    ...
    self.assertEqual((line["allowance"], line["use"], line["left"], line["over"], line["warned_at"]), (4.0, 5.0, -1.0, True, "2026-11-06T10:00:00Z"))

def test_a_budget_raised_after_the_warning_ends_over_and_keeps_the_time_of_the_warning(self):
    # as above, then set_budget 100 (no new pass): over is False, left is 35.0, warned_at stays
    ...

def test_a_new_budget_shows_at_once_without_a_pass(self):
    # one pass with no budget; then set_budget and set_percent; the view has the allowance at once
    ...

def test_without_a_budget_the_use_is_there_and_no_allowance(self):
    ...

def test_before_the_first_pass_of_a_new_week_the_use_is_zero_and_updated_is_none(self):
    # a pass in W1; the clock moves into W2 with no pass: week is W2, every use is 0, updated is None,
    # and W1 is not yet in "past" (it is not closed)
    ...

def test_past_weeks_come_newest_first_with_the_numbers_they_were_closed_with(self):
    # W1: budget 10, parts 40, parts uses 5, curl uses 1 -> closed by a pass in W2; then budget 100 and parts 10
    v = self.share()
    [past] = v["past"]
    self.assertEqual((past["start"], past["end"], past["budget"], past["used"]), (W1, W2, 10.0, 6.0))
    self.assertEqual([(l["id"], l["name"], l["percent"], l["allowance"], l["use"], l["over"]) for l in past["lines"]],
                     [("parts", "PartHub", 40.0, 4.0, 5.0, True), ("curl", "Curlberry", None, None, 1.0, False)])
    self.assertEqual(past["lines"][0]["warned_at"] is not None, True)

def test_at_most_12_past_weeks_are_given(self):
    # write 14 closed week rows by SQL; the view gives the 12 newest
    ...

def test_a_project_no_longer_in_the_config_keeps_its_id_as_its_name_in_a_past_week(self):
    ...

def test_an_id_that_comes_twice_is_listed_once_and_a_project_named_unassigned_holds_the_unassigned_line(self):
    self.cfg["projects"] += [{"id": "parts", "name": "Again", "repos": []}, {"id": "unassigned", "name": "Mine", "repos": []}]
    self.turn("c", self.loose, "2026-11-06T09:00:00Z", 2)
    self.at("2026-11-06T10:00:00Z")
    self.collect()
    v = self.share()
    self.assertEqual([(l["id"], l["name"]) for l in v["projects"]], [("parts", "PartHub"), ("curl", "Curlberry"), ("unassigned", "Mine")])
    self.assertEqual(v["projects"][2]["use"], 2.0)
    self.assertIsNone(v["unassigned"])

def test_without_project_management_there_is_no_project_line(self):
    # patch the offers "projects" and "sessions" of project_management to return []: projects == [], unassigned is None, budget still shown
    ...
```

`RouteTest(ShareHome)` serves `C.Handler` on port 0 in a thread (as `RemoveOneTest.CHILD` and the harness do; stop the server in `addCleanup`) and has `self.call(method, path, body=None, headers=None) -> (status, json)`:

```python
def test_get_gives_the_answer_of_the_offer(self):
    self.at("2026-11-06T10:00:00Z")
    self.collect()
    self.assertEqual(self.call("GET", "/api/share"), (200, self.share()))

def test_a_post_without_apply_true_is_a_dry_run(self):
    code, r = self.call("POST", "/api/share/budget", {"budget": 200, "apply": False})
    self.assertEqual((code, r["applied"], r["ok"], r["steps"], r["manifest"]), (200, False, True, ["weekly budget: not set becomes $200.00"], None))
    self.assertIsNone(self.call("GET", "/api/share")[1]["budget"])

def test_the_three_posts_apply_and_each_writes_a_manifest(self):
    code, r = self.call("POST", "/api/share/budget", {"budget": 200, "apply": True})
    self.assertEqual((code, r["applied"], Path(r["manifest"]).is_file()), (200, True, True))
    code, r = self.call("POST", "/api/share/week-start", {"day": "monday", "time": "05:30", "utc_offset": "+05:30", "apply": True})
    self.assertEqual((code, r["result"]["new"]), (200, [0, 0]))
    code, r = self.call("POST", "/api/share/percent", {"project": "parts", "percent": 40, "apply": True})
    self.assertEqual(code, 200)
    v = self.call("GET", "/api/share")[1]
    self.assertEqual((v["budget"], v["week_start"], v["projects"][0]["percent"]), (200.0, {"day": "monday", "hour": 0}, 40.0))
    code, r = self.call("POST", "/api/share/percent", {"project": "parts", "percent": None, "apply": True})
    self.assertEqual((code, self.call("GET", "/api/share")[1]["projects"][0]["percent"]), (200, None))

def test_a_refused_value_answers_400_with_the_sentence(self):
    code, r = self.call("POST", "/api/share/budget", {"budget": "200", "apply": True})
    self.assertEqual((code, r["error"]), (400, "The weekly budget must be a number above 0, or none to remove it."))
    code, r = self.call("POST", "/api/share/week-start", {"day": "friday", "time": "02:00", "utc_offset": "+05:30", "apply": True})
    self.assertEqual(code, 400)
    self.assertIn("The nearest times that work are 01:30 and 02:30.", r["error"])

def test_a_body_that_lacks_a_key_or_a_true_or_false_apply_answers_400(self):
    for path, body in (("/api/share/budget", {"budget": 200}), ("/api/share/budget", {"budget": 200, "apply": "yes"}),
                       ("/api/share/budget", {"apply": True}), ("/api/share/week-start", {"day": "friday", "apply": True}),
                       ("/api/share/percent", {"project": "parts", "apply": True})):
        self.assertEqual(self.call("POST", path, body)[0], 400, (path, body))
    self.assertIsNone(self.call("GET", "/api/share")[1]["budget"])

def test_another_origin_is_refused_and_another_path_is_not_found(self):
    self.assertEqual(self.call("POST", "/api/share/budget", {"budget": 200, "apply": True}, headers={"Origin": "http://evil.example"})[0], 403)
    self.assertEqual(self.call("GET", "/api/share/nothing")[0], 404)
    self.assertEqual(self.call("POST", "/api/share", {"apply": True})[0], 404)

def test_with_the_store_down_get_answers_503_in_plain_words(self):
    # make ctx.db() raise StoreError as TCM does for the store-down test
    ...
    self.assertEqual(code, 503)
    self.assertTrue(r["error"].startswith("The store is down"))
```

`CommandTest(ShareHome)` runs `share.py`'s `main()` in process with `sys.argv` set and stdout caught (the way `prune_calls` in the harness runs `prune.main`); the CLI module is imported as `import share as CLI` after `sys.path` has `collector/` (it already has):

```python
def test_show_with_no_budget(self):
    self.turn("a", self.parts, "2026-11-06T09:00:00Z", 10)
    self.turn("c", self.loose, "2026-11-06T09:00:00Z", 2.5)
    self.at("2026-11-06T10:00:00Z")
    self.collect()
    code, out = self.run_cli("show")
    self.assertEqual(code, 0)
    self.assertEqual(out.splitlines(), [
        "Week: 2026-11-05 21:00 UTC to 2026-11-12 21:00 UTC (a new week starts every Thursday 21:00 UTC)",
        "Budget: not set. Used so far: $12.50.",
        "PartHub (parts): $10.00 used, no share",
        "Curlberry (curl): $0.00 used, no share",
        "Unassigned (unassigned): $2.50 used, no share"])

def test_show_with_a_budget_and_a_project_over_its_share(self):
    # budget 20, parts 40 (allowance 8), parts uses 10
    ...
    self.assertIn("Budget: $20.00 a week. Used so far: $10.00.", out)
    self.assertIn("PartHub (parts): $10.00 used of $8.00 (40% of the budget), -$2.00 left. All of its share is used.", out)

def test_a_dry_run_prints_the_plan_and_changes_nothing(self):
    code, out = self.run_cli("budget", "200")
    self.assertEqual(code, 0)
    self.assertIn("  [dry-run] weekly budget: not set becomes $200.00", out)
    self.assertIn("Nothing changed. Re-run with --apply to execute.", out)
    self.assertEqual(self.log(), [])

def test_apply_prints_the_manifest_and_the_undo_line_and_the_undo_works(self):
    code, out = self.run_cli("budget", "200", "--apply")
    self.assertIn("  weekly budget: not set becomes $200.00", out)
    self.assertIn("Manifest: ", out)
    undo_line = [l for l in out.splitlines() if l.startswith("Undo:")][0]
    self.assertIn("prune.py restore ", undo_line)
    self.assertTrue(undo_line.endswith(" --apply"))
    stamp = undo_line.split()[-2]
    P.restore(stamp, True)
    self.assertIsNone(self.share()["budget"])

def test_none_removes_and_the_week_start_and_percent_commands_work(self):
    self.run_cli("budget", "200", "--apply")
    self.run_cli("budget", "none", "--apply")
    self.assertIsNone(self.share()["budget"])
    code, out = self.run_cli("week-start", "friday", "02:30", "--utc-offset", "+05:30")
    self.assertIn("week start: stays Thursday 21:00 UTC", out)
    code, out = self.run_cli("percent", "parts", "40", "--apply")
    self.assertEqual(self.share()["projects"][0]["percent"], 40.0)
    code, out = self.run_cli("percent", "parts", "none", "--apply")
    self.assertIsNone(self.share()["projects"][0]["percent"])

def test_a_refusal_prints_its_sentence_and_exits_1(self):
    code, out = self.run_cli("week-start", "friday", "02:00", "--utc-offset", "+05:30", "--apply")
    self.assertEqual(code, 1)
    self.assertIn("The nearest times that work are 01:30 and 02:30.", out)
    self.assertNotIn("Nothing changed", out)
    code, out = self.run_cli("budget", "lots")
    self.assertEqual(code, 1)
```

(The CLI turns the text `none` into `None` and a number text into a `float`; a text that is neither is passed on as it is, so the action refuses it with its own sentence.)

In `tests/test_components.py`: add `"/api/share"` to the `codes` of `CHILD` and add `out["share"] = json.loads(get("/api/share")[1]) if out["codes"]["/api/share"] == 200 else None`. Then assert:
- without Claude management: `out["codes"]["/api/share"] == 404`;
- without Project management: `out["codes"]["/api/share"] == 200`, `out["share"]["projects"] == []`, `out["share"]["unassigned"] is None`, `out["share"]["budget"] is None`;
- without Memory management: 200 and the project line of the fake home's session is there (`[l["id"] for l in out["share"]["projects"]] == []` and `out["share"]["unassigned"]["use"] > 0`, since that home has no config project);
- a new test `test_share_says_so_when_claude_management_is_gone`: `python3 share.py show` in the copy without Claude management exits 1, stdout empty, stderr the one line `Claude management is not loaded`.
- in `BrokenOneTest`, a new test: with Project management broken (a syntax error in `repos.py` after one good pass), the rows of `claude_management_share_weeks` and `claude_management_share_use` are the same before and after the second pass.

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -X dev -m unittest tests.test_share -k ViewTest -k RouteTest -k CommandTest 2>&1 | tail -5`
Expected: FAIL.

- [ ] **Step 3: Implement** `share.view`, the offer, `share_set.handle`, the combined `handle` in `__init__.py`, and `collector/share.py`.

- [ ] **Step 4: Update the offers** asserted in `tests/test_claude_management.py` (`["items", "sessions", "share"]`) and run the whole suite:

Run: `python3 -X dev -m unittest discover -s tests 2>&1 | tail -5`
Expected: OK, no warning, no stray line.

- [ ] **Step 5: Commit**

```bash
git add collector/components/claude_management collector/share.py tests
git commit -m "Weekly share: GET /api/share, the routes that set it, the offer, and the command line share.py"
```

---

### Task 5: One line on the project page and one on Home

**Files:**
- Modify: `dashboard/project.html`, `dashboard/home.html`
- Test: `tests/test_share.py` (class `PagesTest`)

**Interfaces:**
- Consumes: `GET /api/share` (Task 4). A line (`projects[i]` or `unassigned`) has `id`, `name`, `allowance`, `use`, `left`, `over`; the answer has `budget` and `week.end`.

**What shows** (my call: the least change that puts the line where the owner looks):

- **Nothing** on either page when `budget` is null, when the fetch fails for the first time, or (project page) when this project has no allowance.
- **Project page**, one `.notice` with the id `share`, right under the page header, hidden by default (`hidden` attribute). The page asks `/api/share` in its `tick()` (every 10 seconds, like the rest). When a later fetch fails the last line stays.
  - Not over: `Weekly share: $10.00 of $80.00 used. $70.00 left until <the end of the week>.` The three amounts in `.num`.
  - Over: a `.pill.warn` reading `Share used up`, then `<name> has used all of its weekly share: $82.10 of $80.00. Nothing is stopped. A new week starts <the end of the week>.`
  - `<the end of the week>` is `week.end` in the reader's own time zone, through the page's `when()` helper.
- **Home**, in the "needs you" list, after the failed tests: one `<li>` for each line that is over (projects first, then unassigned):
  `<span class="t">PartHub has used all of its weekly share</span><span class="m">$82.10 of $80.00 · a new week starts <day and time></span>`
  - They count in the number beside "needs you" and they add `1 project over its weekly share` (plural: `2 projects over their weekly share`) to the sentence at the top (`#finding`).
  - Home asks `/api/share` in the same `tick()` that asks `/api/projects`. A failed fetch keeps the last answer.
  - The change key of the list (`apKey`) includes the over lines, so the list redraws when one appears or its amount changes.
  - No button, no input, no link is added. Home keeps its own dark look; use its existing classes.
- Every value goes through `esc()`. Money through the page's `money()`.

- [ ] **Step 1: Write the failing tests** (they read the page files as text; the look is checked in the browser in Task 6):

```python
class PagesTest(unittest.TestCase):
    def page(self, name):
        return (TCM.REPO / "dashboard" / name).read_text()

    def test_the_project_page_asks_for_the_share_and_has_one_hidden_line_for_it(self):
        text = self.page("project.html")
        self.assertIn('fetch("/api/share"', text)
        self.assertEqual(text.count('id="share"'), 1)
        self.assertRegex(text, r'<div class="notice" id="share"[^>]*\bhidden\b')
        self.assertIn("has used all of its weekly share", text)
        self.assertIn("Nothing is stopped.", text)

    def test_home_asks_for_the_share_and_lists_a_project_that_used_it_up(self):
        text = self.page("home.html")
        self.assertIn('fetch("/api/share"', text)
        self.assertIn("has used all of its weekly share", text)
        self.assertNotRegex(text, r"<(button|input|select|textarea)\b")       # Home stays voice only

    def test_the_overview_and_the_shell_are_not_changed_by_the_share(self):
        for name in ("index.html", "hierarchy.html", "shell.html"):
            self.assertNotIn("/api/share", self.page(name), name)
```

(If `home.html` holds one of those tags today, drop that assertion and say so in the report: do not change Home to pass it.)

- [ ] **Step 2: Run them and see them fail**

Run: `python3 -X dev -m unittest tests.test_share -k PagesTest -v 2>&1 | tail -8`
Expected: FAIL.

- [ ] **Step 3: Implement** both pages. Read `DESIGN.md` first. Keep each change small: one element and one draw function on the project page; one array and a few lines in `drawNeeds` and `tick` on Home.

- [ ] **Step 4: Run the page tests and the tests that parse the pages**

Run: `python3 -X dev -m unittest tests.test_share tests.test_hardening tests.test_projects tests.test_jarvis 2>&1 | tail -5`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add dashboard/project.html dashboard/home.html tests/test_share.py
git commit -m "Weekly share: one line on the project page and one in Home's needs-you list"
```

---

### Task 6: The documents

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `collector/components/README.md`, `CONTEXT.md`

No code and no test changes. Write in simple English: short sentences, one idea each, the words of `CONTEXT.md`.

- [ ] **Step 1: `CLAUDE.md`.**
  - Commands: add the four `share.py` lines.
  - `Core` paragraph (`app.py`): `Core.restore` runs an undo and logs it. "An action" paragraph: an applied restore writes one line too (`component` `core`, `action` `restore`).
  - Claude management section: the files `share.py` and `share_set.py`; reader three (`share_reader`, round three, and why it is third); the five tables and what each holds; the offers `sessions`, `items`, `share`; the reads `project_management.repos`, `.sessions`, `.projects`; the actions `set_budget`, `set_week_start`, `set_percent`; the undo step `claude_management.share_setting`; the routes. State the rules: the week is kept in UTC and must start on a whole UTC hour; a week is added up fresh at every pass until it has ended, then it is closed and never changes; what a change of the week start does; over means use at or above the allowance and above 0; a warning is made once for a project and a week; with Project management removed there is no project line, and when it gives no session the numbers stay.
  - "The ways in": `collector/share.py`.
  - API routes list: `GET /api/share`, `POST /api/share/budget`, `/api/share/week-start`, `/api/share/percent`.
  - Dashboard: the project page and Home each show one line from `GET /api/share`; the Overview does not.
  - Tests: `tests/test_share.py` and its `ShareHome`.
- [ ] **Step 2: `README.md`.** The components table (Claude management: the weekly share; it now also reads the sessions and the projects from Project management). The diagram: add `share.py  show · budget · week-start · percent` beside `prune.py`. The Layout list: `share.py`, `share_set.py`, `collector/share.py`, and `records.py` ("its tables: sessions, items and the weekly share"). A new short section "The weekly share" with: what it is, in four sentences; the commands to set the budget, the week start and a percent (dry run, then `--apply`); how to undo; where the line shows.
- [ ] **Step 3: `collector/components/README.md`.** "What each component reads from another": add the two new reads of Claude management. Under "Actions": a refusal is `plan.note(...)` and `return False` (the route answers 400 with the note).
- [ ] **Step 4: `CONTEXT.md`.** Extend **Weekly share** (the owner sets one weekly budget and gives each project a percent of it; the unit is the cost Stellaxis works out for each session; the owner sets when the week starts) and add **Weekly budget** and **Warning** as words. In the relationship line "the plan": Project management gives the projects and which session belongs to which project. In "Flagged ambiguities": add "Resolved (2026-11-12): a warning is shown on the pages only."
- [ ] **Step 5: Check the documents against the code** (every route, file name, table name and command in the four files exists as written), then commit:

```bash
git add CLAUDE.md README.md collector/components/README.md CONTEXT.md
git commit -m "Docs: the weekly share in CLAUDE.md, README.md, the component rules and CONTEXT.md"
```

---

## After the tasks (done by the session that runs this plan, not by a task)

1. The whole suite with clean output: `python3 -X dev -m unittest discover -s tests`.
2. One run on a copy of the real data: copy `~/.stellaxis/config.json`, `cache.json` and `stellaxis.db` into a temp folder, set `STELLAXIS_HOME` to it, run `collect.py run` without `--probe`, then `share.py show`. Then set a made-up budget and percents in the temp folder only and show again. Remove the temp folder.
3. A look at the two pages in the browser, served from a temp home on a free port (never 7777), light and dark, desktop and phone width.
4. A final whole-branch review on the strongest model. Fix or record what it finds.
5. Add two sections to the end of this file: "Rulings made while building step 4" (each with its cost if wrong) and "Found while building step 4".

---

## Rulings made while building step 4

Each line is a call the builder made, not the owner. Each says what it costs if it is wrong.

**About what the owner sees and sets**

1. **"Friday 2:30 AM" is read as India time** (Thursday 21:00 UTC). The session that started this work made this call. Cost if wrong: one command sets another start.
2. **The budget is in USD, in the notional API prices Stellaxis already uses.** A normal week of the owner is in the thousands of these dollars (the week of 5 November was $2,730), far above what the plan costs. Cost if wrong: the numbers mean little until the owner picks a budget in this unit; nothing breaks.
3. **The budget, the week start and the percents live in the store, not in `config.json`.** They change only through the three actions. Cost if wrong: they cannot be edited by hand in a file.
4. **A week start that is not on a whole UTC hour is refused.** In India that means a time at half past (02:30, not 02:00). Cost if wrong: a start on a whole India hour needs the cost kept for half hours, and every transcript read again.
5. **A percent is from 0 to 100 for one project. 0 means "warn at the first cent". No percent means "never warn".** Cost if wrong: small; one line of the check.
6. **"Over" needs a use above 0 and a use at or above the allowance. The allowance is rounded to 4 places.** Cost if wrong: with an odd percent a project can count as over 0.00005 dollars early.
7. **A warning stays as a record when the budget is raised later. The pages show "over" from the numbers of now.** So a raised budget takes the line off the page at once, and the history still says when the warning was made. Cost if wrong: none seen.
8. **The line shows on the project page only when the project has an allowance** (a budget and a percent). A project with no percent shows nothing. Cost if wrong: one condition in `project.html`.
9. **On Home the line is one entry in "needs you", and the sentence at the top counts it.** A project that is over stays there until the week ends, so Home is not "All clear" in that time. Jarvis reads that top sentence when asked what needs the owner; it says nothing unasked. Cost if wrong: a few lines in `home.html`. This one is for the owner to confirm.
10. **The Overview is not changed.** Cost if wrong: the Overview shows no share; adding a panel is a later, separate change (that page is also an artifact).
11. **Home shows cents for the two amounts of the line** (its own money helper rounds to whole dollars). Cost if wrong: one line.
12. **The command line is its own file, `collector/share.py`,** not new commands of `prune.py`. Cost if wrong: a rename.
13. **A setting does not start a collection.** After a change of the week start the weeks are added up again at the next collection (within a minute). Cost if wrong: the page shows the old weeks for up to a minute.

**About how the numbers are made**

14. **Claude management owns the share, with a third reader in round three.** Cost if wrong: none seen; this is what the spec says.
15. **The running week is added up fresh at every collection and is kept for good only when it has ended.** Cost if wrong: if a transcript is deleted before the week ends, its cost leaves that week. Claude keeps transcripts 30 days by default and the owner has no shorter setting.
16. **After a change of the week start, the running week is cut at the new start** (or taken into the new week when the new start lies before it), and the warnings of the cut weeks are made again. Cost if wrong: one short week in the history after each change.
17. **A week with no collection at all gets no row.** The final review disagreed: it would fill such weeks from the transcripts. It was left, because filled weeks made while the clock is wrong would then block the pass (ruling 20). Cost if wrong: if the service is off for 7 days or more while Claude is used, those weeks are missing from "past weeks".
18. **A session that Project management does not list counts as unassigned.** The final review disagreed: it would keep the last numbers. It was left, because the other choices fail worse (numbers frozen with no error). Cost if wrong: when Project management's own pass fails, new sessions show under unassigned for that pass; a week that closes in exactly such a pass keeps that split.
19. **When Project management gives no session, or did not load, the share reader changes nothing and adds no second error.** Cost if wrong: a broken Project management shows one error line, not two.
20. **When the clock is behind (a closed week would end after now), the pass changes nothing.** Without this a Mac that starts with a reset clock would delete every past week. Cost if wrong: if the clock stays ahead across a week boundary, the pass also changes nothing until real time passes that week; the fix is to delete that one row.
21. **History starts at the first collection after the merge.** Earlier weeks are not built from the transcripts that are still there. Cost if wrong: "past weeks" is empty for the first week.
22. **The answer gives at most 12 past weeks.** Older ones stay in the store. Cost if wrong: one number.

**About the actions and the core**

23. **One undo step for all three settings** (`claude_management.share_setting`, with `what`, `prev`, `new`). **An undo after a later change still puts the old value back, and says so.** Cost if wrong: an old undo can overwrite a newer value; the line tells the owner.
24. **A new percent for an unknown project id is refused; removing the percent of a project that left the config is allowed.** Cost if wrong: none seen.
25. **The core got `Core.restore` and the helper `undo.folder_of`.** An applied restore is logged with `component` `core`. An applied `purge` is still not logged. Cost if wrong: three lines to move.
26. **A refusal is a note and `return False`, not an exception.** So a refused dry run prints only its sentence. Cost if wrong: none seen.

**About how the work was done**

27. **The builder wrote the four documents and the last fix wave itself,** without a helper, because lean helper use was asked for. The final review read the documents; a small re-review read the fix. Cost if wrong: a mistake there had one reader fewer.
28. **The tests that run `prune` or the routes in process write the config into the fake home and point `prune.ARCHIVE` at the fake archive.** Cost if wrong: none; tests only.

## Found while building step 4

- **The numbers are large.** On the real data the week of 5 November was $2,730.63 in notional API prices (PartHub $1,422, Curlberry $724, Stellaxis $583, Budget App $0, unassigned $1.76). A budget must be picked in this unit.
- **After the merge the service must be restarted.** Until then the running process answers 404 on `/api/share` and the pages show nothing.
- **The first collection after the merge adds change 1 to the store** (five new tables). The copy of the real store took it without an error.
- **An apply from the command line writes its manifest to the real `~/.claude/_archive`,** like every other action. `prune.py archives` lists them; `restore last` can pick one.
- **`--utc-offset -08:00` fails on Python 3.9** (argparse reads it as an option). Write `--utc-offset=-08:00`. A `+` offset works both ways.
- **`prune.ARCHIVE` is worked out at import.** A test that imports `prune` in process must patch it, or it reads the real archive folder.
- **Small points left** (the final review said "leave" to each): `share.update` reads the settings just before its transaction starts, so a setting changed in that instant is used one collection late; `GET /api/share` answers a JSON 500, not 503, when the store is locked; `share.py show` says "the store is down" for any failure of the offer; the store-down test of the reader cannot fail on its own; the page tests are text checks; `drawShare()` in `project.html` is not guarded against an answer with a budget and no project list (the route never sends one).
- **Not done:** no backfill of earlier weeks; no line on the Overview; an applied `purge` is still not logged; the spec's "Open" list still names "how a warning reaches the owner" (the answer is in `CONTEXT.md`).
