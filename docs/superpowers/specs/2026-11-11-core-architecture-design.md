# Stellaxis core architecture: design

Words in this document follow `CONTEXT.md`. Where a line says "my call", the writer chose it and the
owner has not confirmed it. Everything else the owner said or agreed.

## Intent

Stellaxis grew as six parts built one after another: measuring, trimming, the command centre, reel
notes, the skill library and Jarvis. They share a port and a sidebar but not a design, so the owner
could not see how one idea relates to another. This document gives Stellaxis one architecture: a core,
and components that all have the same shape. Every idea of the owner becomes a component in the core,
and every pair of components has a written relationship.

## Goals

1. Stellaxis is one application with one core.
2. Every idea is a component with the same three parts: readers, records, actions.
3. A new idea is added as a new component (or a new function in one), never as a new app with its own
   file, routes and data.
4. Each kind of record has exactly one owner.
5. The four components the owner named fit this shape: Stellaxis, Project management, Claude
   management, Memory management.

## Non-goals

- No new behaviour in the first build steps. They move what exists into the new shape.
- The contents of the project kit, the list of what Memory management may clean, and the design of the
  ways in (pages, Jarvis, Claude apps) are not designed here. Each gets its own design later.
- Reel notes, the skill library and Jarvis are not components yet. They keep running as they are.

## Anti-goals

- A big rewrite that leaves Stellaxis broken for days. The service on port 7777 must work after every
  step.
- A core that needs a component to start.
- A component that reads another component's source, or runs another component's actions.
- Any action that removes or moves something without first showing what it will do.

## Constraints

- Python 3 standard library only. No packages, no build step.
- macOS, the launchd service `com.stellaxis.collector`, port 7777, 127.0.0.1 only.
- All existing tests pass after every step (176 today, `python3 -m unittest discover -s tests`).
- Every existing page, API route, the desktop MCP tool and the artifact path of `index.html` and
  `hierarchy.html` keep working after every step.
- Existing rules stay: every change is a dry run until the owner applies it; the owner decides what
  counts as unused; Stellaxis never installs the approval hook; project folders move only through
  `prune.py relocate`.

## The core

The core is Stellaxis itself (component 1). It has no readers, records or actions of its own. It does
four jobs:

1. **Runs readers.** On each pass it calls every reader of every component.
2. **Keeps the store.** One store holds the records of all components.
3. **Runs actions.** It runs an action for a component, shows the dry run first when the action
   changes or removes files or settings, and writes every applied action to a log. Undo belongs to the
   core too (my call): the manifest that `prune.py` writes under `~/.claude/_archive/<stamp>/` today
   becomes the core's action log, in the same format, so `restore` moves into the core and every old
   archive still restores. No component owns undo.
4. **Serves the ways in.** It is the one HTTP server. Pages, Jarvis and the Claude apps reach
   components only through it.

The core starts and serves with zero components. Removing any one component leaves the core and the
other components working; a component that reads a missing component's records gets an empty result.

## The abstract component

Every component has the same three parts.

| Part | What it is | Rule |
|---|---|---|
| Readers | Code that reads a source on the Mac | Read only. A reader never changes its source. |
| Records | What the component knows, kept in the store | One owner for each kind of record. Only the owner writes it. |
| Actions | What the component can do | Dry run first when it changes or removes files or settings. Every applied action is logged. |

A component also states its name, the records it offers to other components, and the records of other
components that it reads.

A relationship between two components is one thing only: one component reads records that the other
offers. Reading is through what the owner offers, never through the owner's source.

Two components may read the same source when each needs different facts from it (my call). Session
transcripts are the case today: Project management needs which session belongs to which repo, and
Claude management needs what each session used. The core gives a shared helper that reads each new
transcript line one time and hands it to every reader that asked for it. The helper is a tool, not a
reader: it keeps no records.

## The components

### 1. Stellaxis

The core, described above. It is also a project, so Project management manages it like every other
project. It gives its own plugin to Claude, so Claude management manages that plugin like every other
plugin.

### 2. Project management

Manages every project on this Mac, each one on its own.

- A **project** is a name and one or more repos. PartsHub is one project with 12 repos. The word
  "workspace" goes away, in code, config and documents.
- A folder in `~/code` that is in no project is **unassigned**. It stays visible as unassigned and is
  not treated as a project. Unassigned replaces today's "other" group and holds everything that group
  holds, including sessions that ran outside `~/code` (my call). It keeps the task list and routes that
  "other" has today, so nothing is lost in the move (my call).
- **Readers:** repos (git state, branches, worktrees), session transcripts (which session belongs to
  which repo and branch), the desktop app's session records, artifacts.
- **Records:** projects, repos, branches, sessions, artifacts, and for each project which slots of the
  project kit are filled.
- **Actions:** start a session, message a session, approve a request, move a project folder, run a
  project's tests, apply the kit to a project.
- **Exists today:** most of it, in `collector/workspaces.py`, plus `relocate` in `collector/prune.py`.
- **Missing:** the artifact reader (artifacts live on claude.ai, not on the Mac, so how to read them is
  open), and the project kit.

The **project kit** is the set of common tools every project gets. It is defined one time and applied
to every project. It belongs to the project, not to each repo. Slots so far: planning and sprint
management, document library, dedicated agent. The list is open. This document only gives the kit its
place; what each slot holds is a later design.

### 3. Claude management

Manages what Claude has and uses on this Mac, because the Claude desktop application is not flexible
enough for the owner.

- **Functions:** manage the installed skills, manage the installed plugins, manage the connectors,
  manage the Claude plan and give each project a weekly share.
- **Readers:** the Claude setup (`~/.claude`, `~/.claude.json`, installed plugins, each repo's
  `.claude`), session transcripts (what each session used and what it cost).
- **Records:** skills, plugins, connectors and agents, each with where it is installed and how much it
  is used; use and cost for each session; the weekly share of each project; warnings.
- **Actions:** archive a skill, disable a plugin, remove a local connector, set a weekly share, and
  later install a skill in a project.
- **Exists today:** the first three functions, in `collector/collect.py` (inventory and transcript
  parsing), `collector/prune.py` (archive) and `collector/settings_api.py`.
- **Missing:** the weekly share, and the install action. Install exists today only in
  `skills_library/library.py`, which is not placed yet. It stays there and is not moved in step 3. It
  becomes an action of Claude management when the project kit is designed.

Weekly share rules:

- When a project uses all of its weekly share, Stellaxis warns the owner. It does not stop any work.
- The owner sets one weekly budget and gives each project a percent of it (my call). The unit is the
  API-equivalent cost that Stellaxis already computes for each session (my call), because it is not
  known whether a script can read the real weekly limit of the plan.
- The owner sets the day and time the week starts, to match the reset of the plan (my call).
- The percents do not have to add up to 100. The use of unassigned folders shows as its own line and
  has no share unless the owner gives it one (my call).

Connectors are of two kinds. A local connector is set on this Mac, and Stellaxis can remove and
restore it. An account connector is set in the settings of Claude; Stellaxis can only report on it.
Agents are assumed to be in this component too (my call).

### 4. Memory management

Knows what takes disk space on this Mac and cleans safely. "Memory" here means disk space only.

- **Reader:** the disk (sizes of folders).
- **Records:** sizes, and for each folder who owns it (a project, Claude, Stellaxis, or none).
- **Actions:** clean.
- **Rules:** it never touches work that is in use; it never asks the owner to close an application; it
  never treats the project dump in `~/Archive` as disposable; it shows what it will remove before it
  removes it; the owner chooses what is removed.
- **Exists today:** nothing.

What may be offered for cleaning is a later design.

## Relationships

| Reader of records | Owner of records | What is read | Why |
|---|---|---|---|
| Claude management | Project management | Projects, and which session belongs to which project | To count use against each weekly share |
| Project management | Claude management | Skills, plugins and connectors of each project | To show them in the project list |
| Project management | Claude management | The cost of each session | To show what each project and repo cost, as the project pages do today |
| Claude management | Project management | The list of repos | To find the `.claude` folder of each repo |
| Project management | Claude management | The install action's results (later, with the kit) | The kit is made of Claude things, so applying it goes through Claude management's install action (the one case where a component asks another to act; my call) |
| Memory management | Project management | Which project owns a folder, and if it has work in use | So a cleanup never touches work in use |
| Memory management | Claude management | Which transcripts, skills, plugin files and archives are in use | So only the others are offered for cleaning |

Every component depends on the core. The core depends on no component.

If existing behaviour needs a relationship that this table lacks, the builder adds it, as long as it is
a read of offered records, and writes it in this table and in `CONTEXT.md` (my call).

## Names during the move (my calls)

- **"workspace":** the new names are `/api/projects…`, `/project?id=` and the config key `projects`.
  The old routes `/api/workspaces…` and `/workspace?id=`, the `workspaces` key in their answers and the
  file `workspaces.json` stay as aliases until the pages, the tests and the Jarvis agent use the new
  names. The word may remain only in that alias layer, in the spec of 2026-11-06 and in the folder name
  `nexus-workspace`.
- **"project" as Claude uses it:** Claude calls one of its setting scopes "project", and item ids carry
  it (`skill:project:…`). These ids and scope names stay as they are, because they are Claude's own
  words and `prune.py` takes them. The `projects` field of `snapshot.json` (one entry for each folder)
  keeps its shape for as long as that file is written. In the store those entries are repos.

## Not placed yet

These run today and are in no component. They keep working unchanged and are kept apart from the
components in one clearly named place in the code, until the owner names them as ideas.

- Reel notes: `reels/`, the nightly routine, data in `~/Knowledge/reels`.
- Skill library: `skills_library/`, data in `~/Knowledge/skills`. Only its install step touches Claude
  management.
- Jarvis: `agents/jarvis.md`, the voice portal, and the ask, hear and say routes (today inside
  `collector/workspaces.py`).
- The pages in `dashboard/`, the desktop MCP tool and the artifact path. These are ways in.

## Decisions made by the writer (my calls, beyond those marked above)

- **The store is one SQLite file** in `~/.stellaxis`, through the standard library's `sqlite3`. It
  replaces `snapshot.json` and `workspaces.json` as the place of truth and removes the 400-session cap
  of the snapshot. The two JSON files may still be written from the store for as long as a page or the
  MCP tool reads them.
- **Config:** the key `workspaces` becomes `projects` and each entry's `folders` becomes `repos`. The
  old key `projects` (today it holds one Reel Brain notes card) belongs to the reel notes and moves out
  of the way. The config changes only when the owner applies it, after a dry run that shows the old and
  new keys. It never changes by itself at service start. Until then the service reads the old keys. The
  config is backed up before the change.
- **Order of work:** (1) the core with an empty store and zero components; (2) Project management,
  moved from `workspaces.py`; (3) Claude management, moved from `collect.py`, `prune.py` and
  `settings_api.py`; (4) the weekly share; (5) Memory management, after its own design. The project kit
  comes after its own design.

## Left to the builder

- The folder and file layout of the core and the components, and how a component declares itself.
- The tables of the store, and how `cache.json` and `history.jsonl` move into it or stay.
- How a component offers records to another (functions, views or queries), as long as the rule holds:
  read only, through what the owner offers.
- How the old JSON files are kept alive during the move, and when they stop.
- Where a warning is stored, apart from how it reaches the owner (open below).

## How success is checked

1. The core starts and serves with zero components (a test).
2. With any one component removed, the core and the others still run (a test for each). This holds
   from the end of step 3; after step 2 alone, Project management may still take sessions and folders
   from the unmoved `collect.py`.
3. A test fails if a component imports another component's readers or actions. This also holds from
   the end of step 3.
4. All existing tests pass, and every existing route answers as before, after every step.
5. When step 2 is done, the word "workspace" remains only in the places named under "Names during the
   move".
6. `CLAUDE.md` and `README.md` describe this architecture when step 3 is done. The command centre
   spec of 2026-11-06 stays as history.

## Open

- How a warning reaches the owner. The owner opens no Stellaxis page in the morning, so a warning on a
  page alone will not be seen.
- Whether a script can read the real weekly limit of the Claude plan.
- How to read artifacts, which live on claude.ai.
