# Components

One folder per component. The core spec is `docs/superpowers/specs/2026-11-11-core-architecture-design.md`.

## The rules a component follows

The folder
- The folder name is the component name.
- `__init__.py` defines `COMPONENT`, a `core.component.Component` with the same name.
- A component imports only the standard library, `core` and its own folder.
- It never names another component's tables or module. It reaches another component only through the core.
- `ctx.home`, `ctx.claude_dir` and `ctx.claude_json` are the folders. Never read them from the environment.
- A component works with an empty config. With a key missing, it does the least.
- In tests, get a component's module with `core.component.import_folder(folder)`. Never import it by another name.

Readers and the pass
- A pass runs in rounds: the first reader of every component, then the second, and so on. Inside a round the order
  is the component name. Put a reader that needs what another component wrote in this pass in the second place.
- A reader never changes its source.
- `ctx.transcripts()` gives the state of every transcript file. The helper reads each new line one time and hands it
  to the `lines` consumer of every component. `core.transcripts.sessions(files)` groups the files into sessions.
- `fresh()` of a `lines` consumer must give a new dict each time and have no side effects: it is called more than once.
- `ctx.cache` is the pass cache (`cache.json`): bookkeeping only, never records.
- `ctx.memo` is a dict of your own that lives for one pass.

Records and the store
- A component creates only tables and indexes named `<component>_…`. Only the owner writes them.
- A connection from `ctx.db()` is the caller's to close.
- `records` always describes the present shape of the tables. To change a table, edit `records` to the new shape and
  append one change (a list of statements) to `Component.changes` that brings an older store to it. Never edit or
  remove an earlier change. The core keeps the number of changes a store has had in its own table `core_schema`. A
  store with no tables of the component starts at the present shape and runs no change. A change may `ALTER`,
  `DROP ... IF EXISTS`, `UPDATE`, `DELETE FROM` and `INSERT INTO` only tables and indexes named `<component>_…`. It
  runs with the records in one transaction that is rolled back on any error.

Offers and reads
- A component reads another's records only through an offer, with `ctx.read(owner, offer)`, and only when it lists
  that pair in `reads`.
- An offer that raises gives the caller an empty list. The error shows in `GET /api/core`.
- A component that is missing gives an empty list too. A removed component is not an error.
- A component that is there but failed to load is different. `ctx.read` of its offer raises, so the reader that
  asked fails in that pass and its last good files stay. A way in (`Core.read`) still gets an empty list.

Actions
- An action is `fn(plan, ctx, **args)` and returns True on success.
- An action shows what it will do first: with `plan.apply` false it changes nothing.
- An action sets `plan.result` to what its route answers, and says what it skipped with `plan.note`.
- An action refuses a bad value with `plan.note(<one plain sentence>)` and `return False`, and writes nothing. Its route
  answers 400 with the note.
- An action lists in `fn.private` the arguments the action log must not hold.
- A component runs its own action with `ctx.act(action, args, apply)`, so it gets the dry run, the manifest and the
  log line. A way in may hand the action its own plan (`plan=`).
- An action that must be undoable adds a manifest entry whose `op` is `<component name>.<step>`, and declares the
  handler in `Component(undo={...})`. `restore` refuses a manifest with a step that has no handler.
- An undo handler is `(entry, apply, out, ctx)`.
- Actions run also when the store is down; only `ctx.db()` fails.

Routes
- A route reads a JSON body with `core.server.read_body`, which refuses other content types.
- It answers with `core.server.send_json`, and raises `core.server.ApiError` for a refusal.

## What each component reads from another

- `claude_management` reads `project_management.repos`: the list of repos, to find the `.claude` folder of each repo.
- `claude_management` reads `project_management.sessions`: which session belongs to which project, to count use against
  each weekly share. It is read in round three, after Project management wrote it in round two.
- `claude_management` reads `project_management.projects`: the id and name of each project, for the lines of the share.
- `project_management` reads `claude_management.sessions`: the cost of each session.
- `memory_management` reads nothing from another component.
