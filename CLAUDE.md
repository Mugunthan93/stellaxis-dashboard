# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Stellaxis is the base project of this Mac: it manages every existing and new project in `~/code`
(the default home for all projects and Claude sessions, see `~/.claude/CLAUDE.md`). It measures what
Claude Code loads into every session (skills, agents, plugins, MCP servers, CLAUDE.md), what actually
gets used, and what it costs, then helps trim it; it also moves projects safely (`prune.py relocate`).
`reels/` holds the Reel Brain code (data in `~/Knowledge/reels`, served at `/reels/` on port 7777). Python 3 stdlib only, no
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

Everything flows through one file, `$STELLAXIS_HOME/snapshot.json`, written by `collect.py`:

- `collector/collect.py` is the core. `parse_transcripts` reads `~/.claude/projects/**/*.jsonl`
  incrementally (per-file offsets kept in `cache.json`) for usage: `Skill`, `Agent`/`Task`,
  `mcp__server__tool` calls, typed `/commands`, token counts, and the context-listing attachments that
  give *measured* always-on sizes. `inventory` walks `~/.claude`, `~/.claude.json`, installed plugins
  and every discovered project folder (`scan_roots`, plus every cwd seen in transcripts) to list items.
  `build_snapshot` joins the two, prices tokens, and groups projects/worktrees. `collect()` also appends
  to `history.jsonl` and writes `status.json` (current phase, shown by the dashboard).
- `collector/prune.py` and `collector/mcp_server.py` both `import collect as C` and reuse its paths,
  config and snapshot. Changing a snapshot field means checking all three plus both HTML pages.
- `collector/mcp_server.py` is a stdio MCP server with a single read-only tool (`stellaxis_snapshot`),
  registered only in the Claude desktop app, never in Claude Code, so it adds no always-on context. It
  asks the running `serve` process over HTTP (`/api/status`, `/api/refresh`) rather than collecting
  inline, because inline collection inside the app can hang on a macOS folder-permission prompt.
- `collector/cloud_import.py` converts a `list_sessions` (claude-code-remote MCP) dump into
  `cloud.json` for the dashboard's Cloud tab; cloud-container transcripts never reach the Mac.

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

`dashboard/index.html` (Overview) and `dashboard/hierarchy.html` are authored as artifact bodies (no
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
