# Components

One folder per component. The folder name is the component name.
`__init__.py` defines `COMPONENT`, a `core.component.Component` with the same name.
See the core spec for what a component may offer.
A connection from `ctx.db()` is the caller's to close.
An action returns True on success.
In tests, get a component's module with `core.component.import_folder(folder)` and never import it by another name.
A component runs its own action with `ctx.act(action, args, apply)`, so it gets the dry run, the manifest and the log line.
An action that must be undoable adds a manifest entry whose `op` is `<component name>.<step>` and declares the handler in `Component(undo={...})`; `restore` refuses a manifest with a step that has no handler.
Actions run also when the store is down; only `ctx.db()` fails.
A route reads a JSON body with `core.server.read_body`, which refuses other content types, answers with `core.server.send_json`, and raises `core.server.ApiError` for a refusal.
A component imports only the standard library, `core` and its own folder. It never names another component's tables or module.
`ctx.home`, `ctx.claude_dir` and `ctx.claude_json` are the folders; never read them from the environment.
An undo handler is `(entry, apply, out, ctx)`.
An offer that raises gives the caller an empty list; the error shows in `GET /api/core`.
An action sets `plan.result` to what its route answers, says what it skipped with `plan.note`, and lists in `private` the arguments the log must not hold.
`records` always describes the present shape of the tables. To change a table, edit `records` to the new shape and append one change (a list of statements) to `Component.changes` that brings an older store to it. Never edit or remove an earlier change. The core keeps the number of changes a store has had in its own table `core_schema`; a store with no tables of the component starts at the present shape and runs no change. A change may `ALTER`, `DROP ... IF EXISTS`, `UPDATE`, `DELETE FROM` and `INSERT INTO` only tables and indexes named `<component>_…`, and runs with the records in one transaction that is rolled back on any error.
A pass runs in rounds: the first reader of every component, then the second, and so on; inside a round the order is the component name. Put a reader that needs what another component wrote in this pass in the second place.
`ctx.transcripts()` gives the state of every transcript file; the helper reads each new line one time and hands it to the `lines` consumer of every component. `core.transcripts.sessions(files)` groups the files into sessions.
`ctx.cache` is the pass cache (`cache.json`): bookkeeping only, never records. `ctx.memo` is a dict of your own that lives for one pass.
