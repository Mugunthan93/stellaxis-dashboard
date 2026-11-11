# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Stellaxis is the base project of this Mac: it manages every existing and new project in `~/code`
(the default home for all projects and Claude sessions, see `~/.claude/CLAUDE.md`). It measures what
Claude Code loads into every session (skills, agents, plugins, MCP servers, CLAUDE.md), what actually
gets used, and what it costs, then helps trim it; it also moves projects safely (`prune.py relocate`).
`skills_library/` is the Skill Library: `library.py harvest` sparse-clones every GitHub repo the reel notes name,
keeps only the folders holding a `SKILL.md` (data and `catalog.json` in `~/Knowledge/skills`), and `install` copies one
into a project's `.claude/skills/`. The folder is itself the `skill-library` skill, symlinked from `~/.claude/skills/`
so every session can search and install; `/skills` (`dashboard/skills.html`, `GET /api/skills`) shows the catalog.
`reels/` holds the Reel Brain code (data in `~/Knowledge/reels`, served at `/reels/` on port 7777; its `summary.json` feeds the snapshot's `reels` field and the Overview panel). Python 3 stdlib only, no
packages, no build step. Target platform is macOS (launchd service, Claude desktop app config path).

## Commands

```bash
python3 -m unittest discover -s tests                  # all tests (fake-HOME end-to-end, ~1s)
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

The measuring side flows through one file, `$STELLAXIS_HOME/snapshot.json`, written by `collect.py`. The
command centre (Home and Workspace pages) reads a separate index, `workspaces.json`, written in the same pass:

- `collector/collect.py` is the core. `parse_transcripts` reads `~/.claude/projects/**/*.jsonl`
  incrementally (per-file offsets kept in `cache.json`) for usage: `Skill`, `Agent`/`Task`,
  `mcp__server__tool` calls, typed `/commands`, token counts, and the context-listing attachments that
  give *measured* always-on sizes. `inventory` walks `~/.claude`, `~/.claude.json`, installed plugins
  and every discovered project folder (`scan_roots`, plus every cwd seen in transcripts) to list items.
  `build_snapshot` joins the two, prices tokens, and groups projects/worktrees. `collect()` also appends
  to `history.jsonl` and writes `status.json` (current phase, shown by the dashboard). `_cache_loss`
  flags turns that rebuilt the cache after idling past the 1-hour TTL (`cache_lost` per session,
  `cache_loss` in the snapshot, the Overview panel); the threshold was measured from real transcripts.
- `collector/prune.py` and `collector/mcp_server.py` both `import collect as C` and reuse its paths,
  config and snapshot. Changing a snapshot field means checking all three plus both HTML pages.
- `collector/mcp_server.py` is a stdio MCP server with a single read-only tool (`stellaxis_snapshot`),
  registered only in the Claude desktop app, never in Claude Code, so it adds no always-on context. It
  asks the running `serve` process over HTTP (`/api/status`, `/api/refresh`) rather than collecting
  inline, because inline collection inside the app can hang on a macOS folder-permission prompt.
- `collector/workspaces.py` is the command centre. `build_index` writes `workspaces.json`: every session
  (not just the capped snapshot ones) grouped workspace > repo > branch, plus configured folders with no
  sessions as repos with no branches. It also starts and messages sessions, and holds the task lists,
  approvals, plans, routines and test runs. It must not import `collect` (that would be circular);
  `collect.py` passes it `load_config()`.
- `collector/approval_hook.py` is the `PermissionRequest` hook. It runs on the system Python, stays silent
  unless the session's switch is on and it can show the request whole, writes `<id>.req` and waits for
  `<id>.ans`. `approve_ready` looks for it in `settings.json`; Stellaxis never installs it.
- `collector/core/` is the core every component plugs into. It imports nothing outside itself (a test enforces
  it). `component.py` is the `Component` contract and `load()`; `store.py` the one SQLite file, where a component
  creates only its own tables; `app.py` `Core` and `Ctx` (readers, offers, reads, actions run as a dry run or
  applied); `util.py` `read_json`, `write_json`, `short_path`, `now_iso`; `undo.py` `Plan` and `restore` (used
  by `prune.py`); `server.py` `host_ok`, `handle()` and `serve()`. `collect.py` makes it on first use
  (`the_core()`), runs `run_pass` after each collection and offers it every request before `settings_api`.
  `collector/components/` holds component folders and is empty until step 2. Under `$STELLAXIS_HOME`: the store
  `stellaxis.db` and the action log `actions.jsonl`. Route: `GET /api/core`. If the store cannot be opened,
  `status.json` carries `core_error`; components still run and answer, and only what calls `ctx.db()` fails (`StoreError`). The spec has the rest.
- `collector/cloud_import.py` converts a `list_sessions` (claude-code-remote MCP) dump into
  `cloud.json` for the dashboard's Cloud tab; cloud-container transcripts never reach the Mac.

Other files under `$STELLAXIS_HOME`: `tasks/<workspace id>.md` (checkbox lines), `approvals/` (`<id>.req`,
`<id>.ans`, and `on/<session uuid>`, the per-session switch), `test-results/` (last result per repo).

Config keys for the command centre (`config.json`): `claude_bin` (default `~/.local/bin/claude`) and
`workspaces`, a list of `{"id", "name", "folders": [path], "tests": {repo: argv}, "routines": [name]}`.
An entry with no id is skipped. Test commands come from config only, never from a request.

API routes (`workspaces.handle`, all behind `settings_api.host_ok`; POSTs need `application/json`):
`GET /api/workspaces`; `GET` and `POST /api/workspaces/<id>/tasks`; `GET /api/workspaces/<id>/work` (plans,
routines, tests); `POST /api/workspaces/<id>/tests/run`; `POST /api/sessions/new`;
`POST /api/sessions/<uuid>/message`; `GET /api/sessions/<uuid>/reply`; `GET /api/approvals`;
`POST /api/approvals/<id>`; `POST /api/approvals/session/<uuid>`; `GET /api/jarvis` (the briefing the
`agents/jarvis.md` agent writes to `jarvis/briefing.md`); `POST /api/jarvis/ask` (a recording as `audio/*`,
or JSON `{"question"}`: ffmpeg, then `whisper-cli`, then a headless Claude limited to Read/Grep/Glob; one at a
time); `POST /api/jarvis/hear` (a recording: text only), `/api/jarvis/say` (`{"text"}`, spoken by macOS `say`,
returns when done) and `/api/jarvis/hush`. Their binaries are config keys `ffmpeg_bin`, `whisper_bin`,
`whisper_model`, `say_bin` (defaults under `/opt/homebrew/bin`, `~/.stellaxis/models/ggml-base.en.bin` and
`/usr/bin/say`).

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

Routes (`PAGES` in `collect.py`): `/` is `home.html` (what needs you, from `GET /api/workspaces`),
`/overview` is `index.html`, `/workspace?id=<id>` is `workspace.html`. The sidebar's workspace links are
built in `shell.html` from the same API; static links still work if it fails.

`dashboard/home.html` and `workspace.html` are localhost only, not artifacts: they may use absolute `/api`
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

Every `prune.py` command is a dry run until `--apply`. Archives go to `~/.claude/_archive/<stamp>/`
with a manifest so `restore` is exact; plugins are disabled, not uninstalled; JSON configs are backed
up before edits. `relocate` records one `relocate` manifest entry; `restore` undoes it by relocating
back. It rewrites only exact `"cwd":"<path>` / `"project":"<path>` fields, never message text. Desktop
app JSON (`~/Library/Application Support/Claude`) gets every string/key equal to or under the path
rewritten (`remap_tree`), layout kept; tests fake the app state with `STELLAXIS_DESKTOP_RUNNING`. The user decides what counts as unused; never pick a threshold for them, and show
the dry run before applying.

## Plugin

The repo is also a Claude Code plugin marketplace (`.claude-plugin/`) shipping the `stellaxis` skill
(`skills/stellaxis/SKILL.md`) and the `stellaxis-auditor` agent (`agents/`). The skill locates scripts
via `~/.stellaxis/bin` (a symlink to `collector/` created by `collector/install.sh`).

## Tests

`tests/test_e2e.py` builds a fake HOME (skills, agents, commands, a stdio fake MCP server, transcripts,
a git worktree) in `setUpClass`, runs `collect.py run --probe` once as a subprocess, and asserts on the
snapshot. `test_zz_prune_roundtrip` mutates the fake HOME, so it is named to run last.
