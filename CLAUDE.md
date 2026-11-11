# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Stellaxis is the base project of this Mac: it manages every existing and new project in `~/code`
(the default home for all projects and Claude sessions, see `~/.claude/CLAUDE.md`). It measures what
Claude Code loads into every session (skills, agents, plugins, MCP servers, CLAUDE.md), what actually
gets used, and what it costs, then helps trim it; it also moves projects safely (`prune.py relocate`).
It is one core (`collector/core/`) and three components (`collector/components/`); the words are in `CONTEXT.md`.
`skills_library/` is the Skill Library: `library.py harvest` sparse-clones every GitHub repo the reel notes name,
keeps only the folders holding a `SKILL.md` (data and `catalog.json` in `~/Knowledge/skills`), and `install` copies one
into a project's `.claude/skills/`. The folder is itself the `skill-library` skill, symlinked from `~/.claude/skills/`
so every session can search and install; `/skills` (`dashboard/skills.html`, `GET /api/skills`) shows the catalog.
`reels/` holds the Reel Brain code (data in `~/Knowledge/reels`, served at `/reels/` on port 7777; its `summary.json` feeds the snapshot's `reels` field and the Overview panel). Python 3 stdlib only, no
packages, no build step. Target platform is macOS (launchd service, Claude desktop app config path).

## Commands

```bash
python3 -m unittest discover -s tests                  # all tests (fake-HOME end-to-end, ~80s)
python3 -m unittest discover -s tests -k test_usage    # one test

python3 collector/collect.py run [--probe]   # one collection pass + text summary
python3 collector/collect.py serve           # collect every `interval` + dashboard on 127.0.0.1:7777
python3 collector/collect.py summary         # summary of the last snapshot
python3 collector/prune.py list --unused-days 30
python3 collector/prune.py relocate <src> <dest>   # move a project folder (dry run without --apply)
```

To run against something other than the real `~/.stellaxis` and `~/.claude`, set `STELLAXIS_HOME`
(output dir) and `HOME` / `CLAUDE_CONFIG_DIR` (input). The tests do exactly this. `serve` fails with
`Address already in use` when the launchd service from `install.sh` already holds port 7777; restart
that with `launchctl kickstart -k gui/$(id -u)/com.stellaxis.collector`.

## Architecture

Stellaxis is one core and some components. The core runs the readers, keeps the store, runs the actions and
serves the routes. A component is one folder under `collector/components/`. The spec is
`docs/superpowers/specs/2026-11-11-core-architecture-design.md`; the rules a component follows are in
`collector/components/README.md`.

**The core** (`collector/core/`) imports nothing outside itself (a test enforces it).
- `component.py`: the `Component` contract and `load()`. A `Component` has `name`, `readers`, `records`,
  `changes`, `actions`, `offers`, `reads`, `undo`, `handle` and `lines`. `load()` never raises: a folder that
  fails to import becomes a component with an `error`.
- `app.py`: `Core` and `Ctx`. `Core.run_pass` runs a pass; `Core.run_action(component, action, args, apply, cfg,
  plan=None)` runs an action; `Core.read` asks a component's offer for a way in; `Core.undo_handlers` gives the undo
  steps to `restore`; `Core.describe` answers `GET /api/core`. A `Ctx` is what a component sees: `name`, `cfg`,
  `inputs` (the pass inputs), `home`, `claude_dir`, `claude_json`, `service` (true in `collect.py serve`), `db()`,
  `read(owner, offer)` (only a declared read), `act(action, args, apply, plan=None)`, `cache` (`cache.json`),
  `transcripts()`, `memo` (a dict of its own for one pass) and `status(...)`.
- `store.py`: the one SQLite file, `$STELLAXIS_HOME/stellaxis.db`. A component makes only tables named
  `<component>_…`. To change a table, edit `records` and append a step to `changes`; the table `core_schema` counts
  the steps a store has had.
- `transcripts.py`: the transcript helper. It reads each new line of `~/.claude/projects/**/*.jsonl` one time and
  hands it to the `lines` consumer of every component. It keeps the place reached in each file in `cache.json`, in
  the format of before (keys `files`, `ids`, `walk`, `probes`; `"v": "0.2.2"`), so the running service goes on from
  the cache it has. `sessions(files)` gives the plain facts of each session (id, folder, branch, first and last time).
  To make every transcript be read again after a change in how a line is read, change `VERSION` in
  `core/transcripts.py` (a file state with another `v` is read again from the start), or add or rename a key in the
  consumer's `fresh()` (a state without that key is read again too). `VERSION` in `collect.py` is only the version
  the snapshot shows; changing it reads nothing again.
- `undo.py`: `Plan` (the dry run, the steps and the manifest) and `restore`. Each applied plan gets its own folder
  under `~/.claude/_archive/<stamp>-<label>/`. `restore` refuses a manifest with a step that has no handler.
- `server.py`: `host_ok`, `read_body`, `send_json`, `ApiError`, `handle()`, which answers `GET /api/core` and
  then offers the request to each component's `handle`, in name order, and `serve()`, an HTTP server of the core
  alone on 127.0.0.1.
- `util.py`: `read_json`, `write_json`, `short_path`, `parse_duration`, the one clock `now()` (it obeys
  `STELLAXIS_NOW`, for tests) and `now_iso()`.

**A pass.** A collection is one call of `run_pass` with the input `collector` (`version`, `probe`, `reels`,
`project_notes`). The readers of Claude management and Project management return at once without it. The pass runs
in rounds: round one runs the first reader of every component, round two the second, and so on. Inside a round the
order is the component name. So a reader in round two sees what every component wrote in round one. The pass state
(the cache, the transcript files, the memos) belongs to the thread that runs the pass. When the transcript read fails,
the pass does not read the transcripts again: every later reader that asks for them gets the same error, and
`cache.json` is not saved. A reader that raises is an error of its component; the other readers still run.

**An action** is `fn(plan, ctx, **args) -> bool`. With `apply` false it only says what it would do. An applied action
writes one line to `$STELLAXIS_HOME/actions.jsonl`; the names in `fn.private` are left out of that line. A way in may
pass its own `plan=`, so `prune.py` prints exactly what it printed before. An undoable step adds a manifest entry with
`op` `<component>.<step>` and a handler in `Component.undo`; a handler is `(entry, apply, out, ctx)`.

**Claude management** (`collector/components/claude_management/`): what Claude has and uses on this Mac.
- Files: `usage.py` (what each transcript line adds, and one row per session; `_cache_loss` flags turns that rebuilt
  the cache after idling past the 1-hour TTL, a threshold measured from real transcripts), `setup.py` (the inventory:
  `~/.claude`, `~/.claude.json`, installed plugins and each repo's `.claude`; the stdio MCP probes), `snapshot.py`
  (joins setup and use, prices tokens, groups repos and worktrees, `history.jsonl`), `records.py`, `trim.py`
  (archive, move), `settings.py` (the Settings page routes and the save).
- Readers: `usage_reader` (round one: the session rows) and `setup_reader` (round two: the setup, joined with the rows,
  written to `snapshot.json` and `history.jsonl`).
- Records: `claude_management_sessions` (one row per session, with its cost for each hour) and
  `claude_management_items` (one row per item of the last snapshot).
- Offers: `sessions`, `items`. Reads: `project_management.repos` (to find the `.claude` folder of each repo).
- Actions: `archive`, `move` and `save_settings`. The `body` of `save_settings` is private: it never reaches the
  action log. Undo step: `claude_management.settings_file`. The undo of a settings save keeps a copy
  (`.bak-<stamp>`) of a file changed since the save, and puts back the mode of the backup.
- Routes: `GET /api/settings`, `POST /api/settings/preview`, `POST /api/settings/save`.

**Project management** (`collector/components/project_management/`): every project on this Mac.
- Files: `repos.py` (finds the repos: the folders of the sessions, of `~/.claude.json` and of the walk of
  `scan_roots`), `records.py`, `centre.py` (the command centre), `relocate.py` (move a project folder),
  `old_names.py` (the alias layer, below).
- Readers: `repos_reader` (round one: the repos and the sessions) and `index_reader` (round two: the index, written to
  `projects.json` and, under the old names, to the old index file). `build_index` groups every session (not just the
  capped snapshot ones) project > repo > branch; a configured repo with no sessions is a repo with no branches. A path
  in no project belongs to `UNASSIGNED`. It reads only the cost of each session from Claude management (0 when it
  gives none). When round one failed, the last good index stays. A store error does not stop the index files.
- Records: `project_management_repos`, `_runs`, `_projects`, `_index_repos` and `_sessions` (the index without its
  costs).
- Offers: `approvals`, `index`, `live`, `projects`, `repos`, `sessions`. Reads: `claude_management.sessions`.
- Actions: `new_names` (undo step `project_management.old_config_keys`) and `relocate` (undo step
  `project_management.relocate`; an old manifest with the step `relocate` still restores, through one alias in
  `prune.py`).
- Routes: every `/api/projects…`, `/api/sessions…` and `/api/approvals…` route below, through `handle` in
  `__init__.py`, which calls `centre.handle`. The centre also starts and messages sessions, and holds the task
  lists, approvals, plans, routines and test runs. It speaks the new names only. It takes its two folders from a
  context variable that `centre.at(ctx)` sets at every entry, so a thread started inside the centre must be handed
  its paths.
- `old_names.py` is the alias layer: the one place that maps the old names (the old route, page address, index file,
  config keys, request key and group name) to the new ones, and the only code under `collector/` that may write the
  old word (its tests in `tests/test_old_names.py` may too). The old names keep answering until a later step removes
  the layer. `old_names.index` gives the old index file in the new shape; `centre._index` uses it
  (`centre.index_fallback`) only while `projects.json` cannot be read, so pages are not empty before the first pass.

**Memory management** (`collector/components/memory_management/`), read side only: it measures disk space and never
deletes, moves or changes a file. `walk.py` walks the places (never follows a symlink, never enters another volume
or a folder macOS guards, by path and by folder identity; counts a hard-linked file once); `records.py` keeps the
last measurement in `memory_management_runs`, `_folders` and `_skipped`; the reader only checks the clock and
starts one background thread when the last measurement is older than `memory.every` (default 6h), never on the
first pass of a process (so `collect.py run`, `prune.py` and tests that make one pass never measure). Route:
`GET /api/memory`. Config key `memory`: `every`, `max_seconds`, `places` (added to the defaults by name), `guarded`
(only adds). The default places are on only when `$STELLAXIS_HOME` is `.stellaxis` in the account's home folder
(from `pwd`, not `$HOME`), so a test never walks the real disk. The design, with what cleaning may ever offer, is
`docs/superpowers/specs/2026-11-11-memory-management-design.md`.

A component imports only the standard library, `core` and its own folder; nothing outside `collector/core/` imports
a component. A removed component is not an error: its routes answer 404, `GET /api/core` no longer lists it, and a
component that reads its offers gets an empty list. With Project management removed, the snapshot lists no items of
single repos.

A component that is there but fails to load is different. `GET /api/core` lists it with its `error`, and
`ctx.read` of its offers raises (`<owner> did not load: <error>`). So the component that reads it fails too in that
pass and keeps its last good files: with Project management broken, `snapshot.json`, `history.jsonl` and the items
in the store stay as they were; with Claude management broken, `projects.json` stays. `Core.read` (Jarvis and the
other ways in) still gives an empty list. A component whose tables failed at start (for example a store locked by
another writer) is tried again at the start of each pass, and its error clears when that works.

**The ways in.** They hold no component code.
- `collector/collect.py` holds the paths `STX_HOME` and `CLAUDE_DIR`. `the_core()` works out the path of
  `~/.claude.json` when it makes the core, not at import, so a test that points `CLAUDE_DIR` and the env at a fake
  home gets the fake one.
- It also holds `DEFAULT_CONFIG` and `load_config`, and the pass inputs `project_notes` and `reels_summary`.
- `the_core()` makes the core on first use. `collect()` runs one pass and writes `status.json`. It returns the
  snapshot this pass wrote, else `{}`.
- The HTTP `Handler` serves the pages, `/snapshot.json`, `/api/snapshot`, `/api/skills`, `/api/status`,
  `/api/refresh` and Reel Brain. It offers each request to the core first, then to `unplaced/jarvis.py`. `serve()`
  runs the loop and the `Handler`.
- `status.json`: `index_error` holds the errors of every component as `name: error; name: error`; a removed
  component is not one. `last_snapshot` and `elapsed_ms` keep their last values when a pass wrote no snapshot.
  If the store cannot be opened, `status.json` carries `core_error`. Components still run and answer, and actions
  run too; only what calls `ctx.db()` fails (`StoreError`).
- `collect.py run` exits 1 and prints `collect failed: …` when this pass wrote no snapshot and a component failed.
  An older snapshot on disk does not change that. When this pass wrote a snapshot and a component failed, it prints
  the summary, then `collect: …` to stderr, and exits 0.
- `collector/prune.py` is a command line only. `list`, `archives`, `purge` and `restore` stay in it. `archive` and
  `move` run as actions of Claude management, `relocate` as an action of Project management, each through
  `run_action` with a plan `prune.py` builds. A settings save also writes a manifest, so it shows in
  `prune.py archives` and `restore last` can pick it. Without Claude management, `archive` and `move` exit 1 with
  one sentence. `list` lists the last `snapshot.json` when one is on disk and exits 0; it exits 1 only when there
  is none. Without Project management, `relocate` exits 1 with one sentence.
- `collector/mcp_server.py` is a stdio MCP server with a single read-only tool (`stellaxis_snapshot`), registered
  only in the Claude desktop app, never in Claude Code, so it adds no always-on context. It asks the running
  `serve` process over HTTP (`/api/status`, `/api/refresh`) rather than collecting inline, because inline
  collection inside the app can hang on a macOS folder-permission prompt.
- `prune.py` and `mcp_server.py` both `import collect as C` and reuse its paths, config and snapshot. Changing a
  snapshot field means checking Claude management, these ways in, and both HTML pages.

**In no component yet** (they run today):
- `collector/unplaced/jarvis.py` serves the Jarvis routes (briefing, ask, hear, say, hush).
  `handle(handler, method, cfg, core)` reads Project management with `core.read(...)`, so a broken component cannot
  stop it.
- `collector/approval_hook.py` is the `PermissionRequest` hook. It runs on the system Python, stays silent
  unless the session's switch is on and it can show the request whole, writes `<id>.req` and waits for
  `<id>.ans`. `approve_ready` looks for it in `settings.json`; Stellaxis never installs it.
- `collector/cloud_import.py` converts a `list_sessions` (claude-code-remote MCP) dump into
  `cloud.json` for the dashboard's Cloud tab; cloud-container transcripts never reach the Mac.
- `collector/settings_remote.py` and `collector/perm_usage.py` feed the Claude Settings Remote artifact.
- `reels/` and `skills_library/` (above).

Files under `$STELLAXIS_HOME`: `snapshot.json` and `history.jsonl` (Claude management),
`projects.json` and the old index file (Project management), `cache.json` (the transcript helper), `status.json`
(`collect.py`), `stellaxis.db` (the store) and `actions.jsonl` (the action log).

Other files under `$STELLAXIS_HOME`: `tasks/<project id>.md` (checkbox lines; the unassigned list is `tasks/unassigned.md`; `tasks/other.md` is read and written instead while it exists and `tasks/unassigned.md` does not, until the rename is applied), `approvals/` (`<id>.req`,
`<id>.ans`, and `on/<session uuid>`, the per-session switch), `test-results/` (last result per repo).

Config keys for the command centre (`config.json`): `claude_bin` (default `~/.local/bin/claude`) and
`projects`, a list of `{"id", "name", "repos": [path], "tests": {repo: argv}, "routines": [name]}`.
An entry with no id is skipped. Hand-written cards go under `project_notes` (`project_notes` in `collect.py`). Test commands come from config only, never from a request.
The old keys are read until the owner applies the rename: `GET /api/projects/new-names` shows the dry run,
`POST` with `{"apply": true}` applies it, and `prune.py restore <stamp> --apply` undoes it.

API routes (the core's `GET /api/core`; each component's `handle`; and `unplaced/jarvis.py` for the `/api/jarvis`
ones; all behind `core.server.host_ok`; POSTs need `application/json`):
`GET /api/settings`, `POST /api/settings/preview` and `/api/settings/save` (Claude management); `GET /api/memory`
(Memory management); `GET /api/projects`; `GET` and `POST /api/projects/<id>/tasks`; `GET /api/projects/<id>/work` (plans,
routines, tests); `POST /api/projects/<id>/tests/run`; `GET` and `POST /api/projects/new-names` (above);
`POST /api/sessions/new` (`{"project", "folder", "prompt"}`);
`POST /api/sessions/<uuid>/message`; `GET /api/sessions/<uuid>/reply`; `GET /api/approvals`;
`POST /api/approvals/<id>`; `POST /api/approvals/session/<uuid>`; `GET /api/jarvis` (the briefing the
`agents/jarvis.md` agent writes to `jarvis/briefing.md`); `POST /api/jarvis/ask` (a recording as `audio/*`,
or JSON `{"question"}`: ffmpeg, then `whisper-cli`, then a headless Claude limited to Read/Grep/Glob; one at a
time); `POST /api/jarvis/hear` (a recording: text only), `/api/jarvis/say` (`{"text"}`, spoken by macOS `say`,
returns when done) and `/api/jarvis/hush`. Their binaries are config keys `ffmpeg_bin`, `whisper_bin`,
`whisper_model`, `say_bin` (defaults under `/opt/homebrew/bin`, `~/.stellaxis/models/ggml-base.en.bin` and
`/usr/bin/say`).
Every old route and request key still answers through the alias layer.

Home (`/`) is the voice-only Jarvis portal: served without the shell (`FULL_SCREEN` in `collect.py`), no buttons
or inputs. It listens all the time, records whatever is louder than the room until a pause, and acts only on text
starting with "Jarvis" (or within 8s after Jarvis spoke). It ignores the mic while thinking or speaking.
`collector/jarvis_portal.sh` opens it full-screen in a Chrome profile of its own, with autoplay allowed so it
starts without a touch.

### Item ids and token sources

Item ids are `kind:scope[:path]:name` (e.g. `skill:user:foo`, `mcp:local:~/code/x:server`,
`plugin:user:name@marketplace`); `prune.py` takes these ids. Scopes: `user`, `project`, `local`
(per-repo entries in `~/.claude.json`), `account` (claude.ai synced), `connector`, `builtin`. Only
user/project/local items are `removable`; the rest are changed in claude.ai settings.

`tokens_always` has a `source`: `measured` (from transcript context records), `probe` (stdio MCP
`tools/list` size; `probe-failed`/`unknown` when neither works), `estimate`/`file` (bytes / 3.7),
`rollup` (plugin = sum of its parts; plugins are excluded from totals so parts are not counted twice). The "baseline" is real API tokens on each
session's first turn and is the number pruning should move.

### Dashboard

`DESIGN.md` holds the palette, fonts and component rules; read it before changing any page.

Routes (`PAGES` in `collect.py`): `/` is `home.html` (what needs you, from `GET /api/projects`),
`/overview` is `index.html`, `/project?id=<id>` is `project.html`. The sidebar's project links are
built in `shell.html` from the same API; static links still work if it fails.

`dashboard/home.html` and `project.html` are localhost only, not artifacts: they may use absolute `/api`
paths and need no artifact path. `/reels/raw` runs only its own script (`reels_scripts` in `collect.py`
hashes the last script in the file), so that page can't use inline event handlers.

`dashboard/index.html` (Overview) and `hierarchy.html` are authored as artifact bodies (no
`<html>`/`<head>` skeleton). The same file serves both as:
- the localhost page: `collect.py`'s `Handler` wraps it in a skeleton and serves `snapshot.json`,
  `api/status`, `api/refresh` next to it, and
- the Claude desktop artifact: it reads live data via `window.claude.use("mcp")` →
  `host:stellaxis` / `stellaxis_snapshot`, and `cloud.json` is published alongside it.

Keep both paths working when editing a page; relative fetches (`snapshot.json`, `api/...`) must stay
relative.

### Pruning rules (from `skills/stellaxis/SKILL.md`)

Every `prune.py` command is a dry run until `--apply`. Archives go to `~/.claude/_archive/<stamp>-<label>/`
with a manifest so `restore` is exact; plugins are disabled, not uninstalled; JSON configs are backed
up before edits. `relocate` records one `project_management.relocate` manifest entry; `restore` undoes it by
relocating back (an old manifest with the step `relocate` still restores). It rewrites only exact `"cwd":"<path>` / `"project":"<path>` fields, never message text. Desktop
app JSON (`~/Library/Application Support/Claude`) gets every string/key equal to or under the path
rewritten (`remap_tree`), layout kept; tests fake the app state with `STELLAXIS_DESKTOP_RUNNING`. The user decides what counts as unused; never pick a threshold for them, and show
the dry run before applying.

## Plugin

The repo is also a Claude Code plugin marketplace (`.claude-plugin/`) shipping the `stellaxis` skill
(`skills/stellaxis/SKILL.md`) and the `stellaxis-auditor` agent (`agents/`). The skill locates scripts
via `~/.stellaxis/bin` (a symlink to `collector/` created by `collector/install.sh`).

## Tests

Run them all with `python3 -X dev -m unittest discover -s tests`. The output must be clean: no warnings, no stray prints.

`tests/test_same_output.py` is the reference test. It runs `tests/same_output_harness.py` on the present code (one
fake home, two passes, the routes and every prune command, with a fixed clock) and compares the result with
`tests/same_output_expected.json`. The expected file is what the code before step 3 gave (commit `dfb604b`). Never
edit it, or the harness, to make the test pass. To rebuild it, run the harness with `--collector` pointing at a
checkout of `dfb604b` (that code has no `STELLAXIS_NOW`, so the harness patches its clock); the top of the harness
gives the command.

`tests/test_components.py` holds checks 2 and 3 of the spec. `RemoveOneTest` copies `collector/` and `dashboard/`,
deletes one component folder (or all three), and runs a child Python on a fake home: it collects, serves on port 0,
and the core and the other components must still answer. `IndependenceTest` checks that a component imports only
the standard library, `core` and its own folder, and never names another component's tables or module.

`tests/test_e2e.py` builds a fake HOME (skills, agents, commands, a stdio fake MCP server, transcripts,
a git worktree) in `setUpClass`, runs `collect.py run --probe` once as a subprocess, and asserts on the
snapshot. `test_zz_prune_roundtrip` mutates the fake HOME, so it is named to run last.

In tests, get a component's module with `core.component.import_folder(...)` on its folder (the command centre is
`.centre` of the `project_management` folder), never by another import. Never make a temp component folder with
the name of a real one.
A test that applies an action uses `fake_home` from `tests/test_hardening.py`, so no manifest lands in the real `~/.claude`.
An in-process test that runs a collection gives a config with `"scan_roots": []` and patches `live` and `app_records`
of the command centre, so it never walks the real home folder, never runs the real `claude`, and never reads the
desktop app's real records. A child process gets a fake `HOME` and `STELLAXIS_HOME` instead.
