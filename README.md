# Stellaxis

Stellaxis runs on this Mac. It measures what Claude loads into every session, what gets used, and what
it costs. It helps you trim that setup safely. It also manages every project in `~/code` and measures
the disk space of this Mac.

## How it is built

Stellaxis is one **core** and a few **components**. The words come from `CONTEXT.md`.

- The core runs the readers, keeps the store, runs the actions and serves the pages.
- A component is one idea of the owner. Each component has three parts:
  - **readers** read a source on the Mac and never change it;
  - **records** are what the component knows, kept in the store; only the owner writes them;
  - **actions** are what it can do; an action shows a dry run first, and every applied action is logged.
- One component reads another's records only through what the other offers. It never reads the other's source.
- The **store** is one SQLite file, `~/.stellaxis/stellaxis.db`. Each component has its own tables.
- The **ways in** reach the core: the pages on localhost, `prune.py`, the desktop MCP tool, Jarvis.
- The core runs with no component. Remove one component folder and the rest still runs.

The components today:

| Component | What it does | What it reads from another component |
|---|---|---|
| Claude management | Reads the transcripts and the Claude setup, prices the use, writes `snapshot.json`. Archives and moves items, saves settings files. | The list of repos, from Project management. |
| Project management | Finds the repos and sessions, groups them into projects, writes `projects.json`. Runs the command centre (tasks, approvals, sessions, tests). Moves a project folder (`relocate`). | The cost of each session, from Claude management. |
| Memory management | Measures where the disk space goes, in the background. Read only. | Nothing. |

```
 Mac
 ┌────────────────────────────────────────────────────────┐
 │ collect.py serve   (launchd, every 1m)                 │
 │   core: one pass, in rounds, runs every reader         │
 │     Claude management   ─► snapshot.json, history      │
 │     Project management  ─► projects.json               │
 │     Memory management   ─► disk sizes (every 6h)       │
 │   store: ~/.stellaxis/stellaxis.db                     │
 │        │                                               │
 │        ▼                                               │
 │ localhost:7777       mcp_server.py ──► Claude desktop app
 │ (any browser)        (desktop only)    └─► Stellaxis artifact
 │                                                        │
 │ prune.py  list · archive · move · relocate · restore   │
 │      ◄── stellaxis-auditor agent / skill               │
 └────────────────────────────────────────────────────────┘
```

## Install (macOS)

```bash
git clone https://github.com/mugunthan93/stellaxis-dashboard ~/code/stellaxis-dashboard
bash ~/code/stellaxis-dashboard/collector/install.sh
claude plugin marketplace add ~/code/stellaxis-dashboard
claude plugin install stellaxis@stellaxis
```

`install.sh` creates `~/.stellaxis/`, takes a first snapshot, starts the collector service, and
registers the one-tool `stellaxis` MCP server in the Claude desktop app only. It is not added to the
CLI, so it adds nothing to Claude Code's always-on context. Use `--no-service`, `--no-desktop` or
`--uninstall` to change that.

Python 3 stdlib only. No packages.

## What it measures

| Number | Where it comes from |
|---|---|
| **Baseline** | Real API tokens on each session's first turn (input + cache write + cache read). Everything loaded before you type. Pruning should move this. |
| Baseline breakdown | Transcript context records: system prompt, tool schemas per MCP server, skill listing, agent listing, MCP instructions, deferred tool names. |
| Item always-on cost | Measured where the transcript has it; otherwise file size (CLAUDE.md + `@imports`, rules, memory, skill/agent/command listing lines) or a stdio MCP `tools/list` probe. |
| Usage | `Skill`, `Agent`/`Task` (with subagent token totals), `mcp__server__tool` calls, and typed `/commands`, per session and project. |
| Cost | API-equivalent price per model (cache writes 1.25×/2×, cache reads at the model's rate). Notional on Max. Edit `pricing` in `~/.stellaxis/config.json`. |

Scopes: `user` (~/.claude), `project` (repo `.claude/`, `CLAUDE.md`, `.mcp.json`), `local`
(per-repo entries in ~/.claude.json), `account` (claude.ai synced plugins and skills), `connector`
(claude.ai connectors), `builtin` (bundled with Claude Code).

## Trimming

Ask the agent: *"use stellaxis-auditor to audit my setup"*. It proposes **move to project / archive /
keep**, shows a dry run, and applies only after you say yes. You decide what counts as unused. The
dashboard's "Not used in N days" filter and "Copy request for the agent" button help you pick.

Or by hand:

```bash
S=~/.stellaxis/bin
python3 $S/prune.py list --unused-days 30
python3 $S/prune.py archive skill:user:old-thing mcp:user:unused-server        # dry run
python3 $S/prune.py archive skill:user:old-thing mcp:user:unused-server --apply
python3 $S/prune.py move agent:user:planner --to ~/code/mesell --apply
python3 $S/prune.py relocate ~/Documents/app ~/code/personal/app --apply      # move a project folder
python3 $S/prune.py restore last --apply
```

`relocate` moves a project folder and updates everything that knows it by path: Claude transcript
folders and their `cwd` (so `--resume` and Stellaxis history follow), `~/.claude.json` project entries,
prompt history, git worktree links (`git worktree repair`), and the Claude desktop app's Code tab
records (session folders, worktree registry, presets, scheduled tasks). It refuses while a Claude session
runs inside the folder or while the desktop app is open (quit it and run from Terminal), and lists other
files that still mention the old path without editing them.

Archives live in `~/.claude/_archive/<stamp>/` with a manifest. Plugins are disabled, not uninstalled.
JSON configs are backed up before edits. A save on the Settings page also writes a manifest there, so
`prune.py archives` lists it and `restore` can undo it.

## Layout

```
collector/core/           the core; it imports nothing outside itself
  app.py                  Core and Ctx: run a pass, run an action, read an offer
  component.py            the Component contract; loads every component folder
  store.py                the one SQLite file; a component makes only its own tables
  transcripts.py          the shared transcript helper; keeps cache.json
  undo.py                 Plan (dry run and manifest) and restore
  server.py               the routes of the core, and how a component's route is reached
  util.py                 small helpers and the one clock
collector/components/claude_management/
  __init__.py             the component: readers, records, offers, actions, routes
  usage.py                what each transcript line adds; one row per session
  setup.py                the Claude setup: skills, agents, commands, plugins, MCP servers, CLAUDE.md
  snapshot.py             joins setup and use, prices them, writes the totals
  records.py              its tables: sessions and items
  trim.py                 the actions archive and move
  settings.py             the /api/settings routes and the action save_settings
collector/components/project_management/
  __init__.py             the component: readers, records, offers, actions, routes
  repos.py                finds the repos of this Mac
  records.py              its tables: repos and the project index
  centre.py               the command centre: index, tasks, approvals, sessions, plans, tests
  relocate.py             the action relocate: move a project folder
  old_names.py            the old names that still answer, until the rename is applied
collector/components/memory_management/
  __init__.py             the component: the reader, records and GET /api/memory
  walk.py                 measures folder sizes, read only
  records.py              its tables: runs, folders, skipped
collector/collect.py      a way in: runs a collection, serves the pages on localhost
collector/prune.py        a way in: the command line for list, archive, move, relocate, restore, purge
collector/mcp_server.py   a way in: stdio MCP server for the desktop app, one read-only tool
collector/install.sh      macOS service and desktop MCP registration
dashboard/index.html      Overview (same file is the artifact and the localhost page)
dashboard/hierarchy.html  Hierarchy (same file is the artifact and the localhost page)
dashboard/home.html       Home: the voice-only Jarvis portal (localhost only)
dashboard/project.html    Project: sessions, tasks, plans, routines, tests (localhost only)
dashboard/settings.html   Settings: edit user, project and local settings.json (localhost only)
dashboard/skills.html     Skill Library catalog
dashboard/shell.html      the sidebar every localhost page sits in
skills/stellaxis/         plugin skill
agents/                   plugin agents: stellaxis-auditor, jarvis
tests/                    fake-HOME tests (python3 -m unittest discover -s tests)
```

These files run today and are in no component yet:

```
collector/unplaced/jarvis.py   the Jarvis routes: briefing, ask, hear, say, hush
collector/approval_hook.py     the PermissionRequest hook behind "Approve from page"
collector/cloud_import.py      turns a cloud session listing into cloud.json
collector/settings_remote.py   Mac side of the Claude Settings Remote artifact
collector/perm_usage.py        maps each permission rule to the sessions it covers
collector/jarvis_portal.sh     opens the Jarvis portal full-screen
reels/                         Reel Brain code, shown at /reels/
skills_library/                the Skill Library: harvest and install skills
```

## Decisions (2026-10-08)

Scope: CLI + cloud sessions · MacBook only · collector script · every folder with Claude sessions or
files · mesell agent location detected · live tracker · archive before delete · unused threshold is
the user's call · tokens + notional $ · configurable interval (default 1m) · prune via the agent ·
code in this repo · live feed through localhost and desktop MCP.
