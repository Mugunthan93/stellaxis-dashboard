"""The walker: measures folder sizes, read only. It never follows a symlink, never enters another volume and never
opens a guarded folder. No recursion (a thread on macOS has a small C stack); one lstat per entry, and one more per
folder just before it is opened. Run as a script it is the measuring process (see the end of the file)."""
from __future__ import annotations

import fnmatch
import json
import os
import stat
import sys
import threading
import time

GUARDED_HOME = ["Desktop", "Documents", "Downloads", "Pictures", "Movies", "Music", ".Trash",
                "Library/Mail", "Library/Messages", "Library/Safari", "Library/Calendars", "Library/Reminders",
                "Library/Photos", "Library/Containers", "Library/Group Containers", "Library/Daemon Containers",
                "Library/Mobile Documents", "Library/CloudStorage", "Library/Application Support/AddressBook",
                "Library/Application Support/CloudDocs", "Library/Application Support/MobileSync",
                "Library/Application Support/FileProvider", "Library/Caches/com.apple.*", "Library/Caches/CloudKit",
                "Library/Caches/FamilyCircle"]          # under the home folder
GUARDED_ABS = ["/Volumes", "/Network"]
DEPS = {"node_modules", "Pods", "bower_components"}
BUILD = {"dist", "build", ".next", ".nuxt", ".output", "out", "target", ".turbo", ".parcel-cache", ".svelte-kit",
         ".expo", ".angular", "DerivedData", ".gradle", "coverage"}
MAX_SKIPPED = 200
GUARDED = "guarded by macOS"     # left out by design: not a failure, the folders above stay "ok"
LABELLED = {"deps", "venv", "build", "git"}      # nothing below these gets a kind of its own
scandir = os.scandir            # the walker calls walk.scandir(path); tests replace it


class Guard:
    """By path text (touches no disk), and after resolve() also by folder identity (st_dev, st_ino)."""

    def __init__(self, paths: list[str]):
        self.paths = list(paths)
        self.exact: set = set()
        self.patterns: dict = {}
        self.ids: set = set()
        self.names: dict = {}           # (dev, ino) of a folder: the casefolded name patterns guarded inside it
        for p in paths:
            parent, name = os.path.split(p.casefold().rstrip("/") or "/")
            if "*" in name or "?" in name:
                self.patterns.setdefault(parent, []).append(name)
            else:
                self.exact.add(os.path.join(parent, name))

    def hit(self, path: str) -> bool:
        p = path.casefold()
        parent, name = os.path.split(p)
        return p in self.exact or any(fnmatch.fnmatchcase(name, pat) for pat in self.patterns.get(parent, ()))

    def under(self, path: str) -> bool:
        p = path
        while not self.hit(p):
            parent = os.path.dirname(p)
            if parent == p:
                return False
            p = parent
        return True

    def resolve(self) -> None:
        """lstat each guarded folder (and each pattern's parent) once; never one inside a folder guarded by text."""
        self.ids, self.names = set(), {}
        for p in self.paths:
            parent, name = os.path.split(p.rstrip("/") or "/")
            pattern = "*" in name or "?" in name
            if self.under(parent):
                continue
            try:
                st = os.lstat(parent if pattern else p)
            except OSError:
                continue
            if pattern:
                self.names.setdefault((st.st_dev, st.st_ino), []).append(name.casefold())
            else:
                self.ids.add((st.st_dev, st.st_ino))

    def same(self, st, parent, name: str) -> bool:
        """By identity: st is the folder's lstat, parent the (dev, ino) of the folder it is in."""
        return ((st.st_dev, st.st_ino) in self.ids
                or any(fnmatch.fnmatchcase(name.casefold(), pat) for pat in self.names.get(parent, ())))

    def inside(self, path: str) -> bool:
        """By identity, for a place: lstat / and each folder down to path, stopping at the first guarded one."""
        chain = [path]
        while os.path.dirname(chain[-1]) != chain[-1]:
            chain.append(os.path.dirname(chain[-1]))
        parent = None
        for p in reversed(chain):
            try:
                st = os.lstat(p)
            except OSError:
                return False
            if self.same(st, parent, os.path.basename(p)):
                return True
            parent = (st.st_dev, st.st_ino)
        return False


class _Node:
    __slots__ = ("path", "depth", "kind", "labelled", "above", "key", "bytes", "logical", "linked", "files",
                 "changed", "partial", "pending")

    def __init__(self, path: str, depth: int, st, kind, labelled: bool, above):
        self.path, self.depth, self.kind, self.labelled, self.above = path, depth, kind, labelled, above
        self.key = (st.st_dev, st.st_ino)
        self.bytes, self.logical, self.linked, self.files = st.st_blocks * 512, 0, 0, 0
        self.changed, self.partial, self.pending = st.st_mtime, False, None


def _when(t: float):
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    except (OverflowError, OSError, ValueError):
        return None


def _row(path: str, parent, place: dict, kind: str, state: str, n=None) -> dict:
    return {"path": path, "parent": parent, "place": place["name"], "kind": kind, "owner": place["owner"],
            "state": state, "bytes": n.bytes if n else 0, "logical_bytes": n.logical if n else 0,
            "linked_bytes": n.linked if n else 0, "files": n.files if n else 0,
            "changed_at": _when(n.changed) if n else None}


def _worktree_file(path: str) -> bool:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            return b"/worktrees/" in os.read(fd, 512)
        finally:
            os.close(fd)
    except OSError:
        return False


class _Run:
    def __init__(self, guard: Guard, deadline: float, progress: dict):
        self.guard, self.deadline, self.progress = guard, deadline, progress
        self.seen: set = set()
        self.skipped: list = []
        self.skipped_count = 0
        self.troubles = 0               # skips for any reason but GUARDED
        self.files = 0
        self.timeout = False
        self.dev = self.depth = self.repos = None       # of the place being walked

    def skip(self, path: str, reason: str) -> None:
        self.skipped_count += 1
        self.troubles += reason != GUARDED
        if len(self.skipped) < MAX_SKIPPED:
            self.skipped.append({"path": path, "reason": reason})

    def late(self) -> bool:
        self.timeout = self.timeout or time.monotonic() > self.deadline
        return self.timeout

    def scan(self, n: _Node) -> None:
        """Lists a folder: counts its files, queues its sub-folders, decides its kind."""
        n.pending = []
        entries = []
        if self.late():
            n.partial = True
        else:
            try:
                now = os.lstat(n.path) if n.depth else None      # the folder its parent listed, still?
                if now is not None and (not stat.S_ISDIR(now.st_mode) or (now.st_dev, now.st_ino) != n.key):
                    self.skip(n.path, "changed during the walk")
                    n.partial = True
                else:
                    with scandir(n.path) as it:
                        entries = sorted(it, key=lambda e: e.name)
            except FileNotFoundError:
                pass
            except PermissionError:
                self.skip(n.path, "permission denied")
                n.partial = True
            except OSError as e:
                self.skip(n.path, e.strerror or "error")
                n.partial = True
        git = cfg = None
        for e in entries:
            try:
                st = e.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as x:
                self.skip(e.path, x.strerror or "error")
                n.partial = True
                continue
            if st.st_mtime > n.changed:
                n.changed = st.st_mtime
            name = e.name
            if name == ".git":
                git = st
            if stat.S_ISDIR(st.st_mode):
                if st.st_dev != self.dev:
                    self.skip(e.path, "another volume")
                    n.partial = True
                elif self.guard.hit(e.path) or self.guard.same(st, n.key, name):
                    self.skip(e.path, GUARDED)
                else:
                    n.pending.append((e.path, st))
                continue
            n.files += 1
            blocks = st.st_blocks * 512
            if st.st_nlink > 1:
                n.linked += blocks
                key = (st.st_dev, st.st_ino)
                if key in self.seen:
                    continue
                self.seen.add(key)
            n.bytes += blocks
            n.logical += st.st_size
            if name == "pyvenv.cfg" and stat.S_ISREG(st.st_mode):
                cfg = st
        n.pending.reverse()
        self.files += n.files
        self.progress["files"] = self.files
        if n.depth:
            n.kind = self.kind_of(n, git, cfg)

    def kind_of(self, n: _Node, git, cfg):
        name = os.path.basename(n.path)
        kind = None
        if self.repos and not n.labelled:
            if git is not None:
                kind = "worktree" if stat.S_ISREG(git.st_mode) and _worktree_file(os.path.join(n.path, ".git")) else "repo"
            elif cfg is not None:
                kind = "venv"
            elif name in DEPS:
                kind = "deps"
            elif name in BUILD:
                kind = "build"
            elif name == ".git":
                kind = "git"
        return kind or ("folder" if n.depth <= self.depth else None)

    def walk(self, root: _Node) -> list:
        """The nodes of one place that get a row, the place first."""
        nodes, stack = [], [root]
        while stack:
            n = stack[-1]
            if n.pending is None:
                self.scan(n)
            if n.pending:
                path, st = n.pending.pop()
                stack.append(_Node(path, n.depth + 1, st, None, n.labelled or n.kind in LABELLED,
                                   n.path if n.kind else n.above))
                continue
            stack.pop()
            if n.kind:
                nodes.append(n)
            if stack:
                up = stack[-1]
                up.bytes += n.bytes
                up.logical += n.logical
                up.linked += n.linked
                up.files += n.files
                up.changed = max(up.changed, n.changed)
                up.partial = up.partial or n.partial
        return nodes[-1:] + nodes[:-1]


def measure(places: list[dict], guard: Guard, deadline: float, progress: dict) -> dict:
    run, rows, taken = _Run(guard, deadline, progress), [], []
    guard.resolve()
    for p in places:
        real, start = p["path"], len(rows)
        try:
            if guard.under(real):           # by text, before anything on disk is touched
                run.skip(real, GUARDED)
                rows.append(_row(real, None, p, "place", "refused"))
                continue
            real = os.path.realpath(p["path"])
            if not os.path.exists(p["path"]):
                rows.append(_row(real, None, p, "place", "missing"))
                continue
            try:
                st = os.lstat(real)
            except OSError:
                st = None
            low = real.casefold()
            overlap = next((name for path, name in taken if any(a == b or a.startswith(b.rstrip(os.sep) + os.sep)
                                                                for a, b in ((low, path), (path, low)))), None)
            reason = (GUARDED if guard.under(real) or guard.inside(real)
                      else "not a folder" if st is None or not stat.S_ISDIR(st.st_mode)
                      else f"overlaps {overlap}" if overlap else None)
            if reason:
                run.skip(real, reason)
                rows.append(_row(real, None, p, "place", "refused"))
                continue
            taken.append((low, p["name"]))
            if run.late():
                rows.append(_row(real, None, p, "place", "not_reached"))
                continue
            progress["place"] = p["name"]
            run.dev, run.depth, run.repos = st.st_dev, p["depth"], p["repos"]
            for n in run.walk(_Node(real, 0, st, "place", False, None)):
                rows.append(_row(n.path, n.above, p, n.kind, "partial" if n.partial else "ok", n))
            if run.timeout:
                run.skip(real, "time limit")
        except Exception as e:          # one place that fails does not lose the others
            del rows[start:]
            run.skip(real, f"{type(e).__name__}: {e}")
            rows.append(_row(real, None, p, "place", "failed"))
    return {"rows": rows, "skipped": run.skipped, "skipped_count": run.skipped_count, "files": run.files,
            "complete": not run.timeout and run.troubles == 0, "note": "time limit" if run.timeout else None}


# ---- run as a process -------------------------------------------------------------------------------------------
# The component starts this file as a child process (`python walk.py`), so the walk never shares an interpreter with
# the service. It reads one JSON line from stdin: {"places", "guarded", "max_seconds"}. It writes JSON lines to
# stdout: {"progress": {...}} about once a second, then {"result": {...}} or {"error": "..."}. It ends when its stdin
# closes, so it never outlives the process that started it. It only reads the disk, at background priority.

TICK = 1.0


def lower() -> None:
    """Give way to everything else: background priority for the CPU and the disk; plain nice where Python lacks it."""
    try:
        os.setpriority(os.PRIO_DARWIN_PROCESS, 0, os.PRIO_DARWIN_BG)
    except (AttributeError, OSError):
        try:
            os.nice(10)
        except OSError:
            pass


def gone(fd: int) -> None:
    """Wait until the starter closes our stdin (it does when it ends, however it ends), then end at once."""
    try:
        while os.read(fd, 4096):
            pass
    finally:
        os._exit(0)


def main() -> None:
    fd, out, line = sys.stdin.fileno(), sys.stdout, b""
    while not line.endswith(b"\n"):     # the raw descriptor: a thread waiting in a buffered read breaks the shutdown
        got = os.read(fd, 65536)
        if not got:
            return      # the starter went away before it said what to measure
        line += got
    threading.Thread(target=gone, args=(fd,), daemon=True).start()
    lock, done, progress = threading.Lock(), threading.Event(), {}

    def say(obj) -> None:
        with lock:
            out.write(json.dumps(obj) + "\n")
            out.flush()

    def tick() -> None:
        while not done.wait(TICK):
            say({"progress": dict(progress)})

    lower()
    try:
        spec = json.loads(line)
        threading.Thread(target=tick, daemon=True).start()
        result = measure(spec["places"], Guard(spec["guarded"]), time.monotonic() + spec["max_seconds"], progress)
        done.set()
        say({"result": result})
    except Exception as e:
        done.set()
        say({"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
