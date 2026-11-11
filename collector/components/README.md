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
