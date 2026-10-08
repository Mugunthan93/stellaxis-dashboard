---
name: stellaxis
description: Audit Claude context cost (skills, agents, plugins, MCP, CLAUDE.md), refresh the Stellaxis dashboard, archive/move/restore items.
---

# Stellaxis

Stellaxis measures what Claude loads into every session and what actually gets used, then helps trim it.

## Locate the scripts

Use `~/.stellaxis/bin/` (symlink created by `install.sh`). If it is missing, the scripts are in
`collector/` at the plugin root, two levels above this skill's base directory. Below, `$S` means that folder.

## Commands

| Goal | Command |
|---|---|
| Refresh data now | `python3 $S/collect.py run` (add `--probe` to measure stdio MCP schemas) |
| Text summary | `python3 $S/collect.py summary` |
| Ranked items | `python3 $S/prune.py list [--kind skill\|agent\|command\|mcp\|plugin\|claude_md] [--scope user] [--unused-days N]` |
| Archive (dry run) | `python3 $S/prune.py archive <id> ...` |
| Move user item into a repo | `python3 $S/prune.py move <id> ... --to <repo path>` |
| Undo | `python3 $S/prune.py restore last` (or a stamp from `prune.py archives`) |
| Purge old archives | `python3 $S/prune.py purge --older-than 14d` |

Every prune command is a dry run until `--apply` is added. Archives go to `~/.claude/_archive/<stamp>/`
with a manifest, so `restore` is exact.

Dashboard: `http://127.0.0.1:7777` when the collector service runs, or the Stellaxis artifact in the
Claude desktop app (live through the `stellaxis` MCP server). Full snapshot: `~/.stellaxis/snapshot.json`.

## Reading the numbers

- **Baseline** (`totals.baseline_latest`) is real API tokens on a session's first turn: everything loaded
  before the user types. It is the number pruning should move.
- `tokens_always` per item: `source=measured` comes from transcript context records (exact bytes,
  ~3.7 bytes/token); `estimate`/`file` come from file sizes; `probe` is a stdio MCP schema size.
- MCP servers and CLAUDE.md files usually dominate. Skill bodies cost nothing until invoked; only their
  one-line listing is always on.
- `removable: false` items (claude.ai account plugins and skills, connectors, built-ins) are managed in
  claude.ai settings. Name the setting instead of editing files.

## Rules when changing someone's setup

1. The user decides what counts as unused. Show evidence (calls, sessions, last used, projects) and let
   them choose. Never pick a threshold for them.
2. Always show the dry-run output and get an explicit yes before `--apply`.
3. Prefer `move` over `archive` for items used in only one project: global → that repo's `.claude/`.
4. Never edit `~/.claude.json` while the user has Claude Code sessions open without warning them that a
   running session can overwrite it; `prune.py` backs it up inside the archive.
5. After applying, run `collect.py run` and report the baseline before and after.
