# Stellaxis

Stellaxis is the one application that runs on this Mac and holds every idea of its owner as a component.
This file holds the agreed words. Use these words, and only these words, in talk, documents and code.

## Language

**Core**:
The centre that runs and holds every component. The core is Stellaxis itself. It depends on no component.

**Component**:
One idea of the owner, with a name, that lives in the core.
_Avoid_: part, piece, module

**Reader**:
The part of a component that reads a source on the Mac. A reader never changes its source.

**Record**:
One thing a component knows, kept in the store. Each kind of record has one owner, and only the owner writes it.

**Action**:
One thing a component can do. An action that changes or removes files or settings shows what it will do first.

**Store**:
The one place in the core that holds the records of all components.

**Source**:
Something on the Mac, or in the Claude account, that a reader reads. Examples: repos, session transcripts, the Claude setup, the disk.

**Way in**:
A way for the owner to reach Stellaxis: pages, Jarvis, the Claude apps. No idea of the owner covers the ways in yet.

**Stellaxis**:
Component 1. It is the core, and it is also a project that is developed inside itself. It is the only component with no relationship to another component.

**Project management**:
Component 2. It manages every project on this Mac, each one on its own. It has two halves: the project list and the project kit.

**Project**:
A name and one or more repos. Example: PartsHub is one project with 12 repos.
_Avoid_: workspace

**Repo**:
One code folder in `~/code` that belongs to a project.

**Unassigned**:
The group of folders in `~/code` that belong to no project. A folder stays here until the owner gives it a project.

**Project list**:
Every project and what belongs to it: context, skills, sessions and artifacts.

**Project kit**:
The common tools that every project gets. It is defined one time and applied to every project. Slots so far: planning and sprint management, document library, dedicated agent. The list is open.

**Claude management**:
Component 3. It manages what Claude has and uses on this Mac, because the Claude desktop application is not flexible enough. Functions: manage the installed skills, manage the installed plugins, manage the Claude plan, manage the connectors.

**Skill**:
A set of instructions that Claude can load. A skill is installed for the whole Mac or for one project.

**Plugin**:
A package that adds skills, agents or tools to Claude.

**Connector**:
A link from Claude to an outside service, for example Slack, Gmail or Canva. There are two kinds: an account connector is set in the settings of Claude, and a local connector is set on this Mac.
_Avoid_: MCP server (use it only when the technical detail matters)

**Claude plan**:
The Claude subscription of the owner. It has a limit on use for each week.

**Weekly budget**:
The amount the owner sets for one week of Claude use, in USD. Its unit is the cost Stellaxis works out for each session, because the real limit of the **Claude plan** cannot be read. The owner also sets the day and time the week starts.

**Weekly share**:
The part of the **weekly budget** that the owner gives to one project, as a percent. The percents need not add up to 100. When a project uses all of its weekly share, Stellaxis warns the owner. It does not stop work.

**Warning**:
A record that one project used all of its weekly share in one week. It is made once for a project and a week, and it is shown on the pages only.

**Memory management**:
Component 4. It knows what takes disk space on this Mac, and it cleans safely. Here "memory" means disk space only. Rules: it never touches work that is in use, it never asks the owner to close an application, it never treats the project dump in `~/Archive` as disposable, and it shows what it will remove before it removes it.

**Session**:
One Claude conversation. A session belongs to a repo and is counted in the project of that repo.

**Relationship**:
A written statement of how one component relates to another. Each new component gets one with every component already in the core.

## Relationships

- The **core** holds every **component**. A component depends on the core. The core depends on no component.
- Every **component** has the same three parts: **readers**, **records** and **actions**. The core runs the readers, keeps the **store** and runs the actions.
- A **relationship** between two components is one thing only: one component reads **records** that the other offers. It never reads the other's **source**.
- A **project** owns one or more **repos**.
- The **project kit** belongs to the **project**, not to each **repo**. PartsHub gets one sprint plan, one document library and one agent.
- **Sessions** and tests stay with their **repo** and are added together for the **project**.
- **Stellaxis** and **Project management**: Project management lives in the core. Stellaxis is also a project, so Project management manages it like every other project.
- **Stellaxis** and **Claude management**: Claude management lives in the core. Stellaxis gives its own plugin to Claude, so Claude management manages that plugin like every other plugin.
- **Project management** and **Claude management**, installed things: Claude management owns every installed **skill** and **plugin** and knows where each one is installed. The **project list** shows the skills and plugins of each project, but does not own them.
- **Project management** and **Claude management**, the kit: the **project kit** is made of Claude things (an agent, skills, routines). Claude management installs them in each project.
- **Project management** and **Claude management**, the plan: Claude management owns the **Claude plan** and each **weekly share**. Project management gives the projects, and which **session** belongs to which project; the sessions use the share. The use of **unassigned** folders is its own line and has no share unless the owner gives it one.
- **Project management** and **Claude management**, connectors: Claude management owns every **connector**. The **project list** shows which connectors each project uses. Each connector adds load to every session, so a connector that no project uses takes from every **weekly share**.
- **Project management** and **Claude management**, cost and repos: Project management reads the cost of each **session** from Claude management. Claude management reads the list of **repos** from Project management.
- **Stellaxis** and **Memory management**: Memory management lives in the core. The data of Stellaxis itself takes disk space, so Memory management counts it like all other data.
- **Project management** and **Memory management**: the repos, worktrees and sessions of each project take disk space. Memory management asks Project management which project owns a folder, and if that project has work in use, before it cleans.
- **Claude management** and **Memory management**: session transcripts, downloaded skills, plugin files and archives take disk space. Claude management says which of them are in use. Memory management cleans only the others.

## Flagged ambiguities

- "workspace" and "project" were used for the same thing. Resolved: the word is **project**. The old word remains only as old names that still answer (old routes, the old page address, the old index file) and in `config.json` until the owner applies the rename.
- "Stellaxis" means both the core and component 1. Resolved: they are the same thing in two roles.
- Skills show in two components. Resolved: **Claude management** owns them. **Project management** only shows which project has which skill.
- "memory" can mean disk space, RAM, or the notes Claude keeps between sessions. Resolved: in **Memory management** it means disk space only.
- "project" is also the name Claude gives to one of its setting scopes (as in `skill:project:…`). Resolved: that use stays, because it is Claude's own word. Everywhere else **project** means a name and one or more repos.
- Open: **unassigned** is assumed to also hold sessions that ran outside `~/code`, until the owner says no.
- Open: the owner named skills, plugins and connectors. Agents are assumed to be in **Claude management** too, until the owner says no.
- Open: "connector" is assumed to mean both kinds, account and local, until the owner says no.
- What happens when a project uses all of its **weekly share**. Resolved: Stellaxis warns the owner only.
- How a **warning** reaches the owner. Resolved (2026-11-12): it is shown on the pages only, on the project's page and on Home.
- "week" for the **weekly budget**. Resolved (2026-11-12): it starts Friday 02:30 in India (Thursday 21:00 UTC) until the owner changes it.
