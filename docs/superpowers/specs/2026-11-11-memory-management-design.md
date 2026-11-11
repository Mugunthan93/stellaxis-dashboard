# Memory management: design

Words in this document follow `CONTEXT.md`. The main design is
`2026-11-11-core-architecture-design.md`; this document fills its section "4. Memory management".
Where a line says "my call", the writer chose it and the owner has not confirmed it. Every "my call"
is listed again near the end with what it costs if it is wrong.

## Intent

The disk of this Mac is almost full. On 2026-11-11 it held 245 GB and 10.3 GB were free. The owner
cannot see what takes the space, so he cannot choose what to remove.

Memory management does two jobs, in this order:

1. **Measure.** It knows how big each folder is and who owns it.
2. **Clean.** It removes what the owner chooses, safely.

Only job 1 is built now. Job 2 is designed here and is not built. The owner must answer the questions
at the end before any cleaning is built.

## Rules

These are agreed. Nothing below may break them.

1. It never touches work that is in use.
2. It never asks the owner to close an application.
3. It never treats the project dump in `~/Archive` as disposable.
4. It shows what it will remove before it removes it. The owner chooses what is removed.
5. The owner decides what counts as unused. The product never picks a threshold for him.
6. Every change is a dry run until the owner applies it.

Rule added by this design (my call):

7. Measuring only reads. It reads the size and the change time of a file, not what is inside it.
   There is one exception: the small `.git` file of a worktree, to see that the folder is a worktree.
   The records hold folder paths and numbers. They hold no file contents. They hold no file names,
   except where one file could not be read and is listed as not measured.

## What is measured

### The disk as a whole

One line: how big the disk is and how much is free. Example from this Mac: 245 GB, 10.3 GB free.
The number is the one `df` shows. Finder can show more free space, because Finder counts space that
macOS can clear by itself.

### Places

A **place** is one folder that Memory management measures. The list of places is in code and can be
changed in `config.json`. These are the defaults (my call). The sizes are from this Mac on 2026-11-11.

| Place | Folder | Owner | Size here |
|---|---|---|---|
| code | `~/code` | Project management | 26.2 GB |
| claude | `~/.claude` | Claude management | 4.3 GB |
| claude_app | `~/Library/Application Support/Claude` | Claude management | 16.6 GB |
| stellaxis | `~/.stellaxis` | Stellaxis | 0.4 GB |
| knowledge | `~/Knowledge` | none | 1.5 GB |
| archive | `~/Archive` | none | 3.7 GB |
| xcode | `~/Library/Developer` | none | 0.2 GB |
| android_sdk | `~/Library/Android` | none | 18.0 GB |
| android | `~/.android` | none | 3.2 GB |
| caches | `~/Library/Caches` | none | more than 6 GB |
| homebrew | `/opt/homebrew` | none | 9.7 GB |
| applications | `/Applications` | none | not measured yet |
| local | `~/.local` | none | 7.2 GB |
| pnpm, npm, bun, yarn | `~/Library/pnpm`, `~/.npm`, `~/.bun`, `~/.yarn` | none | 2.7 GB |
| cache | `~/.cache` | none | 0.6 GB |
| rustup, cargo | `~/.rustup`, `~/.cargo` | none | 2.5 GB |
| m2, gradle, cocoapods | `~/.m2`, `~/.gradle`, `~/.cocoapods` | none | 0.1 GB |
| nvm | `~/.nvm` | none | 0.4 GB |
| ollama | `~/.ollama` | none | 2.5 GB |
| docker | `~/.docker` | none | 0.1 GB |
| vscode | `~/.vscode` | none | 1.1 GB |

A default place that does not exist on the Mac shows as "missing". That is not an error.

Together these places hold about 107 GB (`/Applications` was added after that count and is not in
it). The disk has about 235 GB in use. The rest, about 128 GB, is shown as one number, "elsewhere".
Memory management does not guess what is in it. This much is known about it on this Mac:

- About 33 GB is in no folder of the owner. It is the other parts of the same disk: macOS itself, its
  recovery copy and its swap space.
- About 95 GB is in folders outside the places. Some of them macOS guards (see below). Many it does
  not: the applications, the rest of `~/Library/Application Support` (Chrome's data alone is 8 GB),
  `/Library`, and whatever else lies in the home folder.

So more than half of the used space is still one number. That is the largest gap of the read side.
The next step for it is under "Known limits".

### Inside a place

Each place is broken down a fixed number of levels. Example: `~/.claude` is broken down two levels, so
`~/.claude/projects/<one folder for each repo>` each get a size. That is where the transcripts are.

The code place is broken down more, because that is where a developer's disk grows:

| Kind | How it is found | Example |
|---|---|---|
| repo | the folder holds a `.git` folder | `~/code/ph-admin` |
| worktree | the folder holds a `.git` file that points into `.git/worktrees/` | `~/code/stellaxis-dashboard/.claude/worktrees/core-step-5-memory-cc2c1c` |
| deps | the folder is named `node_modules`, `Pods` or `bower_components` | `~/code/ph-admin-ui/node_modules` |
| venv | the folder holds a file `pyvenv.cfg` | `~/code/ph-api-ai/.venv` |
| build | the folder is named like build output: `dist`, `build`, `.next`, `target`, `DerivedData` and others | `~/code/ph-portal-ui/.next` |
| git | the `.git` folder of a repo | `~/code/ph-admin/.git` |

"build" is found by name only. A folder named `build` can also hold files a person wrote. So the kind
says "named like build output" and nothing more. A later cleaning step must check more before it
offers such a folder (see "What may be offered").

Inside a deps, venv, build or git folder nothing more is labelled. A `node_modules` inside a
`node_modules` is not a row of its own.

### How a size is counted

- **Size on disk.** The number is the space the files take on the disk, as the `du` command counts
  it. For most folders that is what a cleanup can free. For files with two names and for cloned files
  it is not; see the next two points. A second number, the logical size, is kept too. They differ for a file with holes, for example
  a virtual machine disk of the Claude desktop app.
- **A file with two names is counted one time.** Some tools give one file several names (hard
  links). On this Mac that is rare: 0.25 GB under `~/code`. The file is counted in the first folder that sees it. Each folder also keeps the
  number "linked": bytes in files that have another name somewhere. Removing that folder may not free
  those bytes.
- **Cloned files are counted in full each time (my call). This is the largest known error in the
  numbers.** macOS can make a copy that shares its data with the original until one of them changes.
  Package managers do this. Example from this Mac: `~/code/budget-app/node_modules` shows 161 MB.
  Each of its three worktrees shows 161 MB again. But almost all of it is one set of data. Four rows
  say 646 MB; the disk holds about 161 MB, and removing one of these folders frees almost nothing.
  This can be seen: macOS can say, for each file, how many bytes belong to that file alone. It takes
  one more question to macOS for each file, through a low-level call (`getattrlist`, reached with
  Python's `ctypes`). It is not built here. A mistake in such a call can crash the whole service, and
  no one watches this first build. It is the next step; see "Known limits".
- **A link to another folder is never followed.** Example: `~/.stellaxis/bin` is a link to
  `collector/`. It counts as a link, a few bytes, and the walk does not go through it.
- **Another disk is never entered.** If a folder inside a place is a different volume, it is skipped
  and listed with the reason.
- **Last change.** Each folder also keeps the time of the newest change inside it (my call). This is a
  fact, not a judgement. It is there so the owner can decide later what he calls unused.

### What is never opened

macOS guards some folders. When a program opens one, macOS asks the owner for permission, and the
program waits. The Stellaxis service has no window, so it would wait for ever. Memory management
never opens these folders, not as a place and not below a place:

`~/Desktop`, `~/Documents`, `~/Downloads`, `~/Pictures`, `~/Movies`, `~/Music`, `~/.Trash`,
`~/Library/Mail`, `~/Library/Messages`, `~/Library/Safari`, `~/Library/Calendars`,
`~/Library/Reminders`, `~/Library/Photos`, `~/Library/Containers`, `~/Library/Group Containers`,
`~/Library/Daemon Containers`, `~/Library/Mobile Documents`, `~/Library/CloudStorage`,
`~/Library/Application Support/AddressBook`, `~/Library/Application Support/CloudDocs`,
`~/Library/Application Support/MobileSync`, `~/Library/Application Support/FileProvider`,
`~/Library/Caches/com.apple.*`, `~/Library/Caches/CloudKit`, `~/Library/Caches/FamilyCircle`,
`/Volumes`, `/Network`.

The list is in code. `config.json` can add to it and cannot shorten it (my call). The check ignores
upper and lower case, as the disk does. It also knows a guarded folder by what it is, not only by how
its path is written. So the same folder reached by another path is still never opened.

So these folders are not measured. Their size is part of "elsewhere". If the owner wants them
measured, that is a question for him (see the end).

## Who owns a folder

The owner of a folder comes from its place (my call). Everything under `~/code` belongs to Project
management. Everything under `~/.claude` and the desktop app's data belongs to Claude management.
`~/.stellaxis` belongs to Stellaxis. All other places have no owner.

Memory management does not know which project a repo belongs to. That is a record of Project
management. Today Project management offers no records yet, so the answer shows the repo folder and
the owner "project_management", and nothing more. When Project management offers "which project owns
this folder", Memory management reads it. It never reads Project management's config. It does walk
`~/code`, the same source Project management reads, for different facts (sizes). The main design
allows two components to read one source that way.

## Records

Memory management owns three kinds of record in the store.

| Record | Table | What one row is |
|---|---|---|
| Measurement | `memory_management_runs` | One finished measurement: when, how long, the disk total and free, whether it was complete. |
| Folder size | `memory_management_folders` | One folder of one measurement: path, parent, place, kind, owner, size on disk, logical size, linked bytes, number of files, last change, state. |
| Not measured | `memory_management_skipped` | One folder that could not be measured, with the reason. |

Folder sizes and "not measured" rows are kept for the last measurement only (my call). One row for
each measurement is kept for ever, so the growth of the whole disk can be shown later. That is about
1,500 small rows a year.

**Offered to other components (later, not built):** `sizes`, the folder sizes of the last
measurement. Project management could show the disk use of each project with it. Nothing reads it
today, so it is not built today.

## The route

`GET /api/memory` answers with JSON. It only reads the store and never measures.

- `state`: `never` before the first measurement is stored, then `measured`.
- `measured_at`, `started_at`, `seconds`, `complete`, `note`: the last measurement. `complete` is
  false when the time limit cut it short or a folder could not be read.
- `measuring`: `null`, or when it started, which place it is in, and how many files it has seen.
- `disk`: total, free and used bytes at the time of the measurement.
- `places`: one row for each place, with its state: `ok`, `partial`, `missing`, `refused`,
  `failed` or `not_reached`.
- `folders`: the rows below the places.
- `elsewhere_bytes`: used space of the disk minus the sum of the places.
- `not_measured`: the folders that could not be measured, and why. At most 200 are listed;
  `not_measured_count` is the full number. Example reasons: "permission denied", "guarded by macOS",
  "another volume", "changed during the walk", "time limit". A folder macOS guards is listed here, but
  it does not make the measurement incomplete: it is left out on purpose.
- `idle`: why nothing is being measured, when that needs saying. Example: no places are set.
- `error`: the error of the last measurement that failed, if it failed.

The route answers at once also when no measurement is ready. Then `state` is `never`.

## How measuring runs

The core runs every reader in the collection pass, every minute. A walk of all places takes about one
minute on this Mac (2 million files). It may not run inside the pass. So:

- The reader only looks at the clock. If the last measurement is older than `every` (default 6 hours,
  my call), it starts one measurement in the background and returns. This takes less than a
  millisecond.
- Only one measurement runs at a time. A pass that finds one running starts nothing.
- A measurement stops after `max_seconds` (default 15 minutes, my call). What it has is stored and
  marked `partial`. The places it did not reach are marked `not_reached`.
- Nothing is written to the store while it walks. The result is written in one step at the end. If the
  service stops in the middle, the old result stays and nothing is half written.
- A measurement that fails is tried again after `every`, not after one minute.
- The first pass of a process never starts a measurement (my call). `collect.py run`, `prune.py` and
  the desktop MCP tool each make one pass in a process of their own. A walk started there could not
  finish. So the service measures, from its second pass on, one minute after it starts.

Measured on this Mac: while the walk ran, other work in the same process was not slower (0.162 s with
the walk, 0.166 s without).

**The default places are used only when Stellaxis's data folder is `.stellaxis` in the home folder
(my call).** The home folder here is the home of the Mac account, as macOS knows it. It is not the
`HOME` setting, which a program can change. In the service this is so (`~/.stellaxis`). The tests keep
their data in a temp folder, and some tests also set a false `HOME`; in both cases the defaults are
off, so a test never walks the real disk. Places named in `config.json` are always used.

### Config

One key, `memory`, in `config.json`. Every part has a default in code.

```json
"memory": {
  "every": "6h",
  "max_seconds": 900,
  "places": [{"name": "chrome", "path": "~/Library/Application Support/Google", "depth": 1}],
  "guarded": ["~/Private"]
}
```

- `places` adds to the defaults. An entry with the name of a default changes what it names (for
  example the path) and keeps the rest. An entry with `"off": true` removes it (my call). A path must
  be a full path or start with `~/`. Each entry has `name`, `path`, and may have `owner`, `depth`
  (levels to break down, default 1) and `repos` (label repos, worktrees, deps, venv and build inside
  it).
- A place inside another place is refused, so nothing is counted twice.
- `guarded` adds folders that are never opened.

## Cleaning (designed here, not built)

### Who may clean what

A component never runs another component's actions. That is a rule of the core. So:

- **Folders with no owner** (caches, Xcode, simulators, Homebrew, package managers): Memory
  management cleans them with its own action, `clean`.
- **Folders of a project** (under `~/code`): Memory management may offer one only if Project
  management says two things about it: which project owns it, and that it has no work in use. If
  Project management is missing or gives no answer, the folder is not offered. Where removing needs
  more than deleting a folder (a worktree must be removed with `git worktree remove`), the action
  belongs to Project management, and Memory management only shows the size next to it (my call).
- **Folders of Claude** (`~/.claude`, the desktop app's data): the same, with Claude management. It
  says which transcripts, skills, plugin files and archives are in use. Only the others can be
  offered.
- **Stellaxis's own data** (`~/.stellaxis`): each component cleans its own data. The store and the
  config are never offered.

### What may be offered

Three classes (my call). The class decides how the removal works.

**A. Can be rebuilt.** A tool made it and can make it again.

- Package manager caches: npm, pnpm, bun, pip, Homebrew downloads, CocoaPods, Playwright browsers.
  Where the tool has its own cleaning command, it is used only if it can first show what it will
  remove, as `brew cleanup -n` does (my call). A command that cannot show that first, such as
  `npm cache clean --force`, is not used; then the folder that was shown is deleted.
- Xcode: `DerivedData`, simulator caches, simulators that Xcode marks unavailable.
- Inside a repo: deps, venv and build folders. Only if git ignores the folder (`git check-ignore`),
  so a hand-written `build` folder is never offered. Only if Project management says the repo has no
  work in use.

**B. Finished work that is also somewhere else.**

- A worktree whose branch is merged or pushed and that has no uncommitted change. Project management
  owns the check and the action.
- Old versions that a tool keeps of itself, for example an updater's leftover download.

**C. The only copy.** Removing it loses something for good.

- Old session transcripts, old Stellaxis archives under `~/.claude/_archive` (these are the undo data
  of earlier actions). Whether these may ever be offered is a question for the owner.

### What may never be offered

- `~/Archive/project-dump-2026-08-20`, and all of `~/Archive` (my call: the whole folder, not only
  the dump).
- Any folder macOS guards. It is not even measured.
- Source: any file in a repo that git does not ignore. Any `.git` folder.
- A repo or worktree with uncommitted changes, unpushed commits or a stash. A repo with no remote.
- Anything in use.
- Anything whose owner component is missing or did not answer.
- The store, `config.json`, Claude's settings, skills, plugins in use, and Claude's memory files.
- Anything outside the places. Anything that needs `sudo`. Anything on another disk.

### How "in use" is detected

"In use" is a fact that can be checked now (my call). It is not an age. "Not changed for 60 days" is not "in
use" or "not in use"; it is a threshold, and thresholds are the owner's.

| Fact | How it is checked | Who checks |
|---|---|---|
| A running program works in the folder | `lsof` shows a process whose working folder or open file is inside it. Example: a dev server started in `~/code/ph-portal-ui`. | Memory management |
| A Claude session is live in the repo or worktree | the session's process is running | Project management |
| The tool that owns a cache is running | Xcode or a booted simulator for Xcode's folders; Docker for Docker's; `brew`, `npm`, `pnpm` or `pip` running for their caches | Memory management |
| The Claude desktop app is running | its process exists; then its data is in use | Claude management |
| A transcript belongs to a live or resumable session | Claude management's records | Claude management |

Rules for the check:

- It is done when the list is shown, and again just before each removal. Something that came into use
  in between is skipped.
- When a check fails or cannot run, the answer is "in use".
- In use means skipped, and the list says why. The owner is never asked to close anything.

Known gap: an editor window that is open on a repo, with no terminal and no running program, is not
seen. That is why class A holds only things a tool can rebuild.

So this design reads "in use" narrowly: something runs in the folder now, or a session is live, or the
work in it exists nowhere else. A project the owner worked on an hour ago, with all work pushed and
nothing running, is not in use by this reading. Its deps, venv and build folders could be offered.
Whether that is what the owner means is question 9.

### What a cleaning action looks like

1. **The list.** The owner sees every folder that may be offered: path, owner, size, how many of the
   bytes are linked, last change, class, and whether it can be undone. It is sorted by size. Nothing
   is ticked.
2. **The owner chooses.** He ticks folders, or gives his own rule ("everything in class A not changed
   for 60 days"). The product has no default rule.
3. **The dry run.** It shows exactly what will be removed, how much space that frees, and for each
   item one of two sentences: "This can be put back." or "This cannot be undone."
4. **Apply.** Only what the dry run showed is removed. An item that changed after the dry run, or
   came into use, is skipped and reported. The action runs through the core, so it is written to the
   action log like every other action.

### Undo, said plainly

Freeing space and undo do not go together. A file that can be put back still takes its space.

- **Class A and B: no undo (my call).** The folder is deleted. For class A the dry run shows the
  command that rebuilds it, for example `pnpm install`, and says it needs time and the network. For
  class B everything hangs on the check "this is also somewhere else". If that check is wrong one
  time, work is lost for good. See question 9.
- **Class C: two steps (my call).** Step one, "set aside": the folder is moved into a holding folder
  on the same disk. This can be undone by moving it back, and it frees no space. Step two, "empty":
  the holding folder is deleted. This frees the space and cannot be undone. The owner does step two
  when he has seen that nothing is missing.

## What this needs from others

**From Project management (offers that do not exist yet):** which project owns a folder; whether a
repo or worktree has work in use (live session, uncommitted or unpushed work); an action to remove a
finished worktree.

**From Claude management (does not exist as a component yet):** which transcripts, skills, plugin
files and archives are in use; whether the desktop app is running.

**From the core:**

- A rule for changing a table. Today a table can only be created. This design chose its columns with
  that in mind, but a later change still has no path.
- A way for a reader to do slow work. Today each reader runs inside the pass, so this component
  starts its own background thread. A second slow component would have to copy that.
- `Ctx` gives the Stellaxis data folder but not Claude's folder. This component reads the
  `CLAUDE_CONFIG_DIR` setting itself to find `~/.claude`.
- For cleaning: the gaps already listed in the step 1 plan (`Ctx.act`, a manifest folder that is
  unique, undo steps owned by a component).

## Known limits

- **Cloned files are counted in full each time** (see "How a size is counted"). Next step: one more
  number for each folder, "bytes that removing this folder alone frees".
- **"Elsewhere" is more than half of the used space** (see "Places"). Next step: let a place hold
  another place, with the inner one taken out, so "the rest of the home folder" can be one row.
- **A measurement that waits on the disk cannot be stopped from outside.** If macOS showed a
  permission prompt in spite of the guarded list, that measurement would wait until the service
  restarts. The service and the collection pass keep working.
- **No one has run the default places under the service yet.** On the first start, look at
  `GET /api/memory`. If `measuring.place` stays the same and `started_at` gets old, the walk waits on
  a prompt in that place. Add the folder to `memory.guarded` and restart the service.
- **Only the service measures.** A process that makes one pass (`collect.py run`, `prune.py`) never
  measures. One case is left: the desktop MCP tool collects in its own process when the service is
  down. If it does that twice in one process, the second pass can start a walk there. "Only one at a
  time" holds inside one process, not across two.
- **The free space is as old as the last measurement**, up to 6 hours.
- **Nothing limits the number of rows in the code place.** Every folder named like build output gets
  a row. Today there are about 800. One odd repo could add thousands.
- **A folder with very many files needs much memory for a moment** (a few hundred MB for 500,000
  files in one folder). Not seen on this Mac.
- **Xcode's simulator files for the whole Mac** (`/Library/Developer/CoreSimulator`) are not a place.

## My calls, and what each costs if wrong

| # | Call | Cost if wrong |
|---|---|---|
| 1 | The default places (the table above). | A place the owner cares about is missing until one line is added to `config.json`. No data is lost. |
| 2 | Guarded folders are never measured, so Documents, Downloads, Pictures, Movies and Docker's disk are inside "elsewhere". | The owner cannot see what may be large folders. Xcode's own cache (`~/Library/Caches/com.apple.dt.Xcode`) is hidden by the same rule. Fixing it needs his permission in macOS settings, and then one config line. |
| 3 | Owner comes from the place, not from each folder. | A folder under `~/code` that is not a project's is still shown as Project management's. Display only. |
| 4 | Sizes are size on disk; a hard-linked file counts once, in the first folder that sees it. | Two folders that share files show different sizes from run to run only if the folder order changes; it does not. Low. |
| 5 | Each folder keeps the time of its newest change. | One unused column. |
| 6 | Folder rows are kept for the last measurement only. | "What grew this week, by folder" cannot be shown until history is kept. The whole-disk history is kept. |
| 7 | Measure again after 6 hours; stop after 15 minutes. | Numbers are staler, or the disk works more often, than the owner likes. One config line. |
| 8 | The defaults are on only when the data folder is `.stellaxis` in the account's home folder. `HOME` is not looked at. | A Stellaxis set up with its data anywhere else, or run with a different `HOME` on purpose, measures nothing until places are named in config. The route says so. |
| 9 | The config key is `memory`; `places` adds to the defaults; `guarded` can only add. | A rename later means one more alias. |
| 10 | Kinds in the code place: deps and build by name, venv by `pyvenv.cfg`, worktree by its `.git` file. | A folder gets the wrong label. Display only today; cleaning checks with git before it offers. |
| 11 | No file names in the records, only folders. | "Which single file is huge" cannot be answered. |
| 12 | The measurement runs as a background thread in the service, not as a separate program. | A stuck measurement cannot be killed without a service restart (see known limits). |
| 13 | Cleaning has three classes, and class C uses "set aside" then "empty". | More steps than the owner wants for things he is sure about. |
| 14 | A repo's deps, venv and build folders are offered only if git ignores them. | A rebuildable folder that git tracks by mistake is never offered. Safe side. |
| 15 | All of `~/Archive` is never offered, not only the project dump. | The owner must remove an old archive by hand. Safe side. |
| 16 | Worktree removal is Project management's action, not Memory management's. | If the owner wants one "clean" button for everything, the page must call two components. |
| 17 | A tool's own cleaning command is used only when it can first show what it will remove. | Some caches are deleted as a folder though the tool has a gentler command. |
| 18 | The offer `sizes` is named but not built. | Project management cannot show disk use of each project until it is. |
| 19 | No page is built. The owner sees the numbers as JSON at `/api/memory`. | He has to read JSON until a page exists. |
| 20 | Cloned files are counted in full. The number "freed by removing this folder alone" is not built yet. | For a cloned folder (a `node_modules` in a worktree) the size shown is far more than a cleanup would free. The owner could pick the wrong thing to remove. Nothing is lost; a rebuild puts it back. |
| 21 | "In use" means: something runs in it now, a session is live, or its work exists nowhere else. It does not mean "a project I am still working on". | A rebuildable folder of a project the owner works on every day could be offered while nothing runs. He would have to rebuild it. |
| 22 | Class A and B are deleted with no undo. | If the check "this worktree is also somewhere else" is wrong one time, that work is lost for good. |
| 23 | The first pass of a process never measures. | The first measurement after a service start comes one minute later. |
| 24 | A folder macOS guards does not make a measurement "incomplete". | A place whose size lacks its guarded folders shows as `ok`. The list of folders not measured still names them. |
| 25 | `/Applications` is a default place. | A longer walk, for sizes the owner may not need. One config line turns it off. |

## Questions only the owner can answer

1. **Guarded folders.** Do you want Documents, Downloads, Pictures, Movies and Music measured? They
   are part of the 128 GB that is "elsewhere". How large a part is not known: about 33 GB of it is
   macOS itself, and much of the rest is in folders that are not guarded. Measuring them needs you to
   give the Stellaxis service "Full Disk Access" in the macOS settings, one time.
2. **Transcripts.** May old session transcripts ever be offered for cleaning? `~/.claude` is 4.3 GB,
   mostly transcripts. They are the only copy of what each session said.
3. **Old archives.** May the undo data of old Stellaxis actions (`~/.claude/_archive`) ever be
   offered? Removing one means that action can no longer be undone.
4. **`~/Archive`.** Is the whole folder off limits, or only `project-dump-2026-08-20`? Example:
   `~/Archive/cleanup-2026-11-04` is not the dump.
5. **The Claude desktop app's data** is 16.6 GB. It is in use whenever the app runs, and the app runs
   almost always. Under rule 2 it can then almost never be cleaned. Is that what you want?
6. **Android** takes 21 GB (`~/Library/Android` and `~/.android`). Do you still build Android apps on
   this Mac? If not, this is the largest single thing a cleanup could offer.
7. **Your rule for "unused".** Do you want to tick folders one by one each time, or give a rule of
   your own that Stellaxis remembers?
8. **How you will look at it.** A page, Jarvis ("what takes my disk?"), or both?
9. **What "in use" means to you.** This design says: something is running in the folder right now, or
   a session is live, or the work exists nowhere else. Or do you mean "a project I am still working
   on", also when nothing runs? And for a finished worktree whose branch is merged or pushed: is
   "deleted, no undo" acceptable?
10. **The rest of the disk.** More than half of the used space is "elsewhere". Should Stellaxis also
    break down the rest of your home folder, or only the developer places it measures today?
