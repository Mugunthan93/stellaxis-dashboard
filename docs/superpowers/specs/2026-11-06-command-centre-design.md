# Stellaxis Command Centre: design and feasibility

## Intent

Stellaxis becomes the one place to run several projects in parallel. One page shows every project,
every session and what needs attention, and sessions can be opened, messaged and started from it.

## Workspaces

Four workspaces, plus "Other" for everything else. The folder grouping is a guess until confirmed.

| Id | Name | Folders under `~/code` |
|---|---|---|
| `parthub` | PartHub | `parthub`, `partshub-ai`, `ph-admin`, `ph-admin-ui`, `ph-api-ai`, `ph-api-ai-wt-api`, `ph-api-ai-wt-demo`, `ph-api-ai-wt-worker`, `ph-lane-d-ev`, `ph-portal-ui`, `ph-portals`, `nexus-workspace` |
| `curlberry` | Curlberry | `curlberry` |
| `budget` | Budget App | `budget-app` |
| `stellaxis` | Stellaxis | `stellaxis-dashboard` |

## What it does

1. **Home:** sessions waiting for you, sessions running, and repos with unpushed or unbacked work.
2. **Workspace page:** Sessions (repo → branch → sessions, newest created first), Tasks, Plans,
   Routines and Tests.
3. **Session actions:** Open, Message and New session.

Overview, Hierarchy, Settings and Reels stay and sit under the new pages.

## Not in scope

Phone access to the page (it listens on 127.0.0.1 only), voice or Jarvis, cleanup, editing plan
documents, and telling other repos' sessions to update the task list.

## Feasibility (tested 2026-11-06, Claude Code 2.1.314, app 4.4212.0)

| Need | Verdict | Evidence |
|---|---|---|
| All sessions by project, branch and time | Yes | Each session has project, branch, start time and cost. `max_sessions` keeps 400 of 1,223 in the snapshot, so a separate index is needed. |
| Session titles | Yes, app sessions only | 161 records in `~/Library/Application Support/Claude/claude-code-sessions`: title, branch, source branch, created time, archived, linked PRs. Not a public format, so read defensively. |
| Live status | Yes | `claude agents --json` took 0.13s and listed 23 sessions with status. It works with no PATH when called by absolute path, which launchd needs. |
| Start a session from the server | Yes | `claude --bg --name <n>` started a session and printed its id. It refuses untrusted folders: `ph-portals` and `ph-admin` must be opened once in Claude first. |
| Message a finished session | Yes | `claude -p --resume <uuid>` kept the context of a throwaway session. |
| Message a running session | Yes, through a relay | `--bg --resume` starts a copy and `-p --resume` is refused, so neither works. A short headless Claude that calls the built-in `SendMessage` does: an idle session woke and replied, and a desktop app session received the message. About $0.002 and 12s per message. |
| See started sessions in the app | Yes, by link | A `--bg` session has no app record, but `claude://resume?session=<uuid>` imported a closed test session into the app. |
| Open or prefill from a link | Yes | `claude://code/continue?session=<app_id>` opened that app session. `claude://code/new?folder=&q=` created no session, so `q` doesn't send at once. |
| Plans | Partly | Only `parthub` has `docs/plans` (5 files). |
| Tasks | New | No project has a task file. |
| Tests | Partly | No past results are stored, so results start from the first run. |
| Routines | Partly | Four tasks in `~/.claude/scheduled-tasks` give name and description. Schedules and run history were not found on disk. |

**How Message works:** each live session lists a socket in `~/.claude/sessions/<pid>.json`
(`messagingSocketPath`). The server looks it up and runs one headless Claude with only `SendMessage`,
which delivers the text to `uds:<socket>`. The text goes in on stdin as data, and a message that tried
to instruct the relay was still delivered intact. Tool search stays off, because its extra turn once
made the relay send the wrong text. "Delivered" is shown only when the recorded tool call matches the
text and recipient exactly and the tool reports success. About $0.002 per message. Closed sessions use
`--bg --resume` instead.

**Limits of Message:**
- It reaches the session as a message from another Claude session, not from you. The session treats it
  as a teammate's request: it can't approve a permission prompt, and the session won't change its
  permissions, CLAUDE.md or config because of it. This is a safety rule in Claude Code. Stellaxis
  doesn't write to the sockets directly or pose as the user, so for approvals you open the session.
- "Delivered" means it reached the session's inbox, not that it was read.
- 4 of 22 local sessions listed no socket, and those can't be messaged. Offline and cloud sessions are
  out of reach too.
- A message to a busy session isn't lost: in the real test it arrived while the session was working
  and was answered.

**How approvals work:** a message can't approve anything, so approvals use Claude Code's own
`PermissionRequest` hook. When a session needs permission, the hook sends the request to Stellaxis and
waits; the page shows the exact command with Allow and Deny, and the hook returns your answer. On a
throwaway background session, Allow ran the command and Deny blocked it, and the session respected
the denial.

**Limits of approvals:**
- Tested on a background session only. Desktop app sessions are not tested, and most of yours run in
  auto mode with 106 always-allow rules, so they rarely ask.
- The hook has to go in `~/.claude/settings.json`, which applies to every session. It's a security
  setting, so it needs the owner's yes.
- While the hook waits, the app's own prompt doesn't appear. So it stays off per session unless you
  switch on "Approve from page", and if the wait times out the normal prompt appears.
- **A session can still approve its own request.** Page approval is a convenience, not a security
  boundary. Sessions run as the same Mac user, and your always-allow rules include `Write`, `Edit`,
  `Bash(cp:*)`, `Bash(tee:*)`, `Bash(npm:*)` and others. So a session (or a subagent working while the
  session waits) could write the answer file or call the approval endpoint, and removing `curl` and
  `nc` doesn't close that. A session that tries it is acting against your instructions, but the design
  doesn't prevent it. A deny rule on `~/.stellaxis/approvals` narrows the gap, not fully.

**Not tested on purpose:** resuming a real session from the command line while it is open in the app.

## Decisions made without asking (check these)

- Task lists live in `~/.stellaxis/tasks/<workspace>.md` as checkbox lines, not in repos, so they
  don't touch branches or worktrees. That folder has no backup.
- Started sessions use Claude's default permission prompts and default model; nothing bypasses them.
- Test runs use a `test_command` set per repo in config, against the main checkout.
