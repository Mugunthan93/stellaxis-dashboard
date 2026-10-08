# Stellaxis

Measures what Claude loads into every session, what actually gets used, and what it costs. Then it
helps you trim it safely.

```
 Mac
 ┌────────────────────────────────────────────┐
 │ collect.py serve   (launchd, every 1m)     │
 │   ~/.claude  ~/.claude.json  repos/.claude │
 │   ~/.claude/projects/**/*.jsonl  (incr.)   │
 │        │                                   │
 │        ▼                                   │
 │   ~/.stellaxis/snapshot.json + history     │
 │        │                    │              │
 │        ▼                    ▼              │
 │ localhost:7777       mcp_server.py ──────► Claude desktop app
 │ (any browser)        (desktop only)        └─► Stellaxis artifact (live)
 │                                            │
 │ prune.py  archive · move · restore ◄── stellaxis-auditor agent / skill
 └────────────────────────────────────────────┘
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
python3 $S/prune.py restore last --apply
```

Archives live in `~/.claude/_archive/<stamp>/` with a manifest. Plugins are disabled, not uninstalled.
JSON configs are backed up before edits.

## Layout

```
collector/collect.py     inventory + incremental usage parser + snapshot + local server
collector/prune.py       archive / move / restore / purge (dry run by default)
collector/mcp_server.py  stdio MCP server, one read-only tool: stellaxis_snapshot
collector/install.sh     macOS service + desktop MCP registration
dashboard/index.html     dashboard (same file is the artifact and the localhost page)
skills/stellaxis/        plugin skill
agents/                  plugin agent: stellaxis-auditor
tests/test_e2e.py        fake-HOME end-to-end test (python3 -m unittest discover -s tests)
```

## Decisions (2026-10-08)

Scope: CLI + cloud sessions · MacBook only · collector script · every folder with Claude sessions or
files · mesell agent location detected · live tracker · archive before delete · unused threshold is
the user's call · tokens + notional $ · configurable interval (default 1m) · prune via the agent ·
code in this repo · live feed through localhost and desktop MCP.
