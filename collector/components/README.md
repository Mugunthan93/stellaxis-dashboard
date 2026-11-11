# Components

One folder per component. The folder name is the component name.
`__init__.py` defines `COMPONENT`, a `core.component.Component` with the same name.
See the core spec for what a component may offer.
A connection from `ctx.db()` is the caller's to close.
An action returns True on success.
In tests, get a component's module with `core.component.import_folder(folder)` and never import it by another name.
