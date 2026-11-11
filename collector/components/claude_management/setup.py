"""The Claude setup: the skills, agents, commands, plugins, MCP servers, rules and CLAUDE.md files of this Mac."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

import core.util
from core.util import parse_duration, read_json, short_path

from .usage import tok


def read_text(p: Path, limit: int = 2_000_000) -> str:
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = re.search(r"^---\s*$", text[3:], re.M)
    if not end:
        return {}, text
    raw, body = text[3:3 + end.start()], text[3 + end.end():]
    meta, key, block = {}, None, None
    for line in raw.splitlines():
        if block is not None and (line.startswith((" ", "\t")) or not line.strip()):
            meta[key] = (meta[key] + ("\n" if block == "|" else " ") + line.strip()).strip()
            continue
        block = None
        m = re.match(r"^([A-Za-z0-9_-]+)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val in ("|", ">", "|-", ">-"):
            block, meta[key] = val[0], ""
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        meta[key] = val
    return meta, body


def item(kind, name, scope, path=None, always=0, on_use=0, **kw) -> dict:
    scope_key = scope if not kw.get("scope_path") else f"{scope}:{kw['scope_path']}"
    if kw.get("plugin"):
        scope_key += f":{kw['plugin']}"
    d = {"id": f"{kind}:{scope_key}:{name}", "kind": kind, "name": name, "scope": scope,
         "path": short_path(path) if path else None, "bytes_always": always, "bytes_on_use": on_use,
         "tokens_always": tok(always), "tokens_on_use": tok(on_use), "removable": True}
    d.update(kw)
    return d


def resolve_imports(path: Path, depth=0, seen=None) -> tuple[int, list]:
    """Bytes of a CLAUDE.md plus its @imports (recursive)."""
    seen = seen or set()
    rp = path.resolve()
    if rp in seen or depth > 5 or not path.is_file():
        return 0, []
    seen.add(rp)
    text = read_text(path)
    total, imports = len(text.encode()), []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        if in_code:
            continue
        for m in re.finditer(r"(?:^|\s)@((?:~|\.{1,2})?/?[\w./-]+\.\w+)", line):
            ref = m.group(1)
            target = Path(os.path.expanduser(ref)) if ref.startswith("~") else (path.parent / ref)
            if target.is_file():
                b, sub = resolve_imports(target, depth + 1, seen)
                total += b
                imports.append(short_path(target))
                imports += sub
    return total, imports


def scan_skill_dir(base: Path, scope: str, scope_path=None, plugin=None, prefix="") -> list:
    out = []
    if not base.is_dir():
        return out
    for sk in sorted(base.iterdir()):
        f = sk / "SKILL.md"
        if not f.is_file():
            continue
        text = read_text(f)
        meta, body = frontmatter(text)
        name = prefix + (meta.get("name") or sk.name)
        desc = meta.get("description", "")
        listing = len(f"- {name}: {desc}".encode())
        if str(meta.get("disable-model-invocation", "")).lower() == "true":
            listing = 0
        aliases = sorted({name, prefix + sk.name} | ({"anthropic-skills:" + sk.name} if scope == "account" else set()))
        out.append(item("skill", name, scope, f, listing, len(text.encode()), scope_path=scope_path,
                        plugin=plugin, description=desc[:300], dir=short_path(sk), aliases=aliases))
    return out


def _origin(url: str) -> str:
    """Scheme and host only: hosted MCP URLs can carry their credential in the path."""
    u = urllib.parse.urlsplit(str(url))
    return f"{u.scheme}://{u.hostname}" if u.hostname else ""


def scan_md_dir(base: Path, kind: str, scope: str, scope_path=None, plugin=None, prefix="") -> list:
    out = []
    if not base.is_dir():
        return out
    for f in sorted(base.rglob("*.md")):
        text = read_text(f)
        meta, body = frontmatter(text)
        rel = f.relative_to(base).with_suffix("")
        name = prefix + (meta.get("name") or ":".join(rel.parts))
        desc = meta.get("description", "")
        if kind == "agent":
            tools = meta.get("tools")
            listing = len(f"- {name}: {desc} (Tools: {tools or '*'})".encode())
        else:
            listing = len(f"- {name}: {desc}".encode()) if desc else 0
        out.append(item(kind, name, scope, f, listing, len(text.encode()), scope_path=scope_path,
                        plugin=plugin, description=desc[:300], model=meta.get("model")))
    return out


def mcp_items(servers: dict, scope: str, path: Path, scope_path=None, plugin=None, disabled=()) -> list:
    out = []
    for name, conf in (servers or {}).items():
        if not isinstance(conf, dict):
            continue
        transport = conf.get("type") or ("stdio" if conf.get("command") else "http" if conf.get("url") else "?")
        target = _origin(conf["url"]) if conf.get("url") else " ".join([str(conf.get("command", ""))] + [str(a) for a in conf.get("args", [])][:4])
        out.append(item("mcp", name, scope, path, 0, 0, scope_path=scope_path, plugin=plugin,
                        transport=transport, target=target[:200], enabled=name not in disabled,
                        conf_hash=hashlib.sha1(json.dumps(conf, sort_keys=True).encode()).hexdigest()[:12],
                        _conf=conf))
    return out


def scan_plugin(root: Path, key: str, scope: str, enabled: bool, scope_path=None) -> list:
    manifest = read_json(root / ".claude-plugin" / "plugin.json", {}) or {}
    pname = manifest.get("name") or key.split("@")[0]
    pre = pname + ":"
    children = []
    children += scan_skill_dir(root / "skills", scope, scope_path, plugin=key, prefix=pre)
    children += scan_md_dir(root / "commands", "command", scope, scope_path, plugin=key, prefix=pre)
    children += scan_md_dir(root / "agents", "agent", scope, scope_path, plugin=key, prefix=pre)
    mcp = (read_json(root / ".mcp.json", {}) or {}).get("mcpServers") or {}
    if isinstance(manifest.get("mcpServers"), dict):
        mcp = {**mcp, **manifest["mcpServers"]}
    children += mcp_items(mcp, scope, root / ".mcp.json", scope_path, plugin=key)
    hooks = read_json(root / "hooks" / "hooks.json", {}) or {}
    for c in children:
        c["enabled"] = enabled and c.get("enabled", True)
    always = sum(c["bytes_always"] for c in children) if enabled else 0
    p = item("plugin", key, scope, root, always, 0, scope_path=scope_path, enabled=enabled,
             version=manifest.get("version"), description=(manifest.get("description") or "")[:300],
             children=[c["id"] for c in children], hooks=sorted((hooks.get("hooks") or {}).keys()))
    return [p] + children


def installed_plugins(claude_dir: Path) -> list:
    """[(key, installPath, scope, projectPath)] from installed_plugins.json (v1 or v2)."""
    data = read_json(claude_dir / "plugins" / "installed_plugins.json", {}) or {}
    out = []
    for key, val in (data.get("plugins") or {}).items():
        entries = val if isinstance(val, list) else [val]
        for e in entries:
            if isinstance(e, dict) and e.get("installPath"):
                out.append((key, Path(e["installPath"]).expanduser(), e.get("scope", "user"), e.get("projectPath")))
    return out


def worktree_of(P: Path):
    """Main repo of a git worktree (its .git is a file pointing into <repo>/.git/worktrees/<name>)."""
    g = P / ".git"
    if g.is_file():
        m = re.match(r"gitdir:\s*(.+)", read_text(g, 4096).strip())
        if m:
            gd = Path(m.group(1).strip())
            gd = gd if gd.is_absolute() else (P / gd).resolve()
            if gd.parent.name == "worktrees":
                return short_path(gd.parent.parent.parent)
    parts = P.parts
    if ".claude" in parts and "worktrees" in parts:
        i = parts.index(".claude")
        return short_path(Path(*parts[:i]))
    return None


def discover(ctx, transcript_cwds: set, claude_json: dict) -> list:
    cfg, cache = ctx.cfg, ctx.cache
    roots = set(p for p in transcript_cwds if p)
    roots |= set((claude_json.get("projects") or {}).keys())
    walk = cache.get("walk") or {}
    if time.time() - walk.get("at", 0) > parse_duration(cfg.get("rescan_every", "1h"), 3600):
        found = set()
        excl = set(cfg.get("exclude_dirs", []))
        for r in cfg.get("scan_roots", []):
            base = Path(os.path.expanduser(r))
            base_depth = len(base.parts)
            for dirpath, dirnames, filenames in os.walk(base):
                depth = len(Path(dirpath).parts) - base_depth
                if depth <= 2:
                    ctx.status("scanning folders", force=False, path=short_path(dirpath))
                dirnames[:] = [d for d in dirnames if d not in excl and not (d.startswith(".") and d != ".claude")]
                if depth >= cfg.get("scan_depth", 6):
                    dirnames[:] = [d for d in dirnames if d == ".claude"]
                if Path(dirpath) == core.util.HOME:
                    dirnames[:] = [d for d in dirnames if d != ".claude"]
                    continue
                if Path(dirpath).name == ".claude":
                    dirnames[:] = []
                    continue
                if ".claude" in dirnames or "CLAUDE.md" in filenames or ".mcp.json" in filenames:
                    found.add(dirpath)
        walk = cache["walk"] = {"at": time.time(), "dirs": sorted(found)}
    strong = set(roots)  # session cwds and ~/.claude.json projects always count
    walked = set(walk.get("dirs", []))
    for d in sorted(walked):
        # a folder with only a nested CLAUDE.md inside another project is that project's lazy-loaded file
        inside = any(d != r and d.startswith(r.rstrip(os.sep) + os.sep) for r in strong | walked)
        if not inside or (Path(d) / ".claude").is_dir():
            strong.add(d)
    return sorted(r for r in strong if Path(r).is_dir() and Path(r) != core.util.HOME and not str(r).startswith(str(ctx.claude_dir)))


def inventory(ctx, transcript_cwds: set) -> tuple[list, list, dict]:
    cfg = ctx.cfg
    items = []
    cj = read_json(ctx.claude_json, {}) or {}
    settings = read_json(ctx.claude_dir / "settings.json", {}) or {}
    settings_local = read_json(ctx.claude_dir / "settings.local.json", {}) or {}
    enabled_plugins = {**(settings.get("enabledPlugins") or {}), **(settings_local.get("enabledPlugins") or {})}

    # user-level memory files
    for f in [ctx.claude_dir / "CLAUDE.md"]:
        if f.is_file():
            b, imports = resolve_imports(f)
            items.append(item("claude_md", "~/.claude/CLAUDE.md", "user", f, b, 0, imports=imports))
    rules = ctx.claude_dir / "rules"
    if rules.is_dir():
        for f in sorted(rules.rglob("*.md")):
            meta, _ = frontmatter(read_text(f))
            b = len(read_text(f).encode())
            cond = bool(meta.get("paths"))
            items.append(item("rule", f.relative_to(rules).as_posix(), "user", f, 0 if cond else b, b if cond else 0,
                              conditional=cond))
    items += scan_skill_dir(ctx.claude_dir / "skills", "user")
    items += scan_md_dir(ctx.claude_dir / "agents", "agent", "user")
    items += scan_md_dir(ctx.claude_dir / "commands", "command", "user")
    items += mcp_items(cj.get("mcpServers"), "user", ctx.claude_json)

    # account-synced (claude.ai) skills and plugins — managed in claude.ai, not on disk
    for bucket in sorted((ctx.claude_dir / "skills" / "synced").glob("*")) if (ctx.claude_dir / "skills" / "synced").is_dir() else []:
        if bucket.is_dir() and not bucket.name.startswith("."):
            for it in scan_skill_dir(bucket, "account"):
                it.update(removable=False, manage="claude.ai → Settings → Capabilities → Skills")
                items.append(it)
    for bucket in sorted((ctx.claude_dir / "plugins" / "synced").glob("*")) if (ctx.claude_dir / "plugins" / "synced").is_dir() else []:
        if bucket.is_dir() and not bucket.name.startswith("."):
            for pdir in sorted(bucket.iterdir()):
                if (pdir / ".claude-plugin" / "plugin.json").is_file():
                    for it in scan_plugin(pdir, pdir.name, "account", True):
                        it.update(removable=False, manage="claude.ai → Settings → Plugins")
                        items.append(it)

    # installed plugins
    for key, ipath, pscope, ppath in installed_plugins(ctx.claude_dir):
        if not ipath.is_dir():
            continue
        enabled = bool(enabled_plugins.get(key, False))
        if pscope in ("project", "local") and ppath:
            ps = read_json(Path(ppath) / ".claude" / "settings.json", {}) or {}
            pl = read_json(Path(ppath) / ".claude" / "settings.local.json", {}) or {}
            enabled = bool({**(ps.get("enabledPlugins") or {}), **(pl.get("enabledPlugins") or {})}.get(key, enabled))
        items += scan_plugin(ipath, key, pscope if pscope != "user" else "user", enabled,
                             scope_path=short_path(ppath) if ppath and pscope != "user" else None)

    # projects
    projects = []
    for proj in discover(ctx, transcript_cwds, cj):
        ctx.status("reading project", force=False, path=short_path(proj))
        P = Path(proj)
        sp = short_path(P)
        wt = worktree_of(P)
        if wt:
            # a worktree's .claude/ is a checkout of the main repo's files; count its sessions, not its items
            projects.append({"path": sp, "abs": str(P), "items": [], "ancestor_claude_md": [], "has_git": True,
                             "worktree_of": wt})
            continue
        pitems = []
        for rel in ["CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md"]:
            f = P / rel
            if f.is_file():
                b, imports = resolve_imports(f)
                pitems.append(item("claude_md", rel, "project", f, b, 0, scope_path=sp, imports=imports))
        pr = P / ".claude" / "rules"
        if pr.is_dir():
            for f in sorted(pr.rglob("*.md")):
                meta, _ = frontmatter(read_text(f))
                b = len(read_text(f).encode())
                cond = bool(meta.get("paths"))
                pitems.append(item("rule", f.relative_to(pr).as_posix(), "project", f, 0 if cond else b,
                                   b if cond else 0, scope_path=sp, conditional=cond))
        pitems += scan_skill_dir(P / ".claude" / "skills", "project", sp)
        pitems += scan_md_dir(P / ".claude" / "agents", "agent", "project", sp)
        pitems += scan_md_dir(P / ".claude" / "commands", "command", "project", sp)
        ps = read_json(P / ".claude" / "settings.json", {}) or {}
        pl = read_json(P / ".claude" / "settings.local.json", {}) or {}
        disabled = set(ps.get("disabledMcpjsonServers") or []) | set(pl.get("disabledMcpjsonServers") or [])
        pitems += mcp_items((read_json(P / ".mcp.json", {}) or {}).get("mcpServers"), "project", P / ".mcp.json", sp,
                            disabled=disabled)
        pj = (cj.get("projects") or {}).get(proj) or {}
        pitems += mcp_items(pj.get("mcpServers"), "local", ctx.claude_json, sp, disabled=set(pj.get("disabledMcpServers") or []))
        # ancestor CLAUDE.md files are loaded too
        ancestors = []
        for anc in P.parents:
            if anc == core.util.HOME or len(anc.parts) <= 1:
                break
            f = anc / "CLAUDE.md"
            if f.is_file():
                ancestors.append(short_path(f))
        # auto-memory
        enc = re.sub(r"[^A-Za-z0-9]", "-", str(P))
        mem = ctx.claude_dir / "projects" / enc / "memory" / "MEMORY.md"
        if mem.is_file():
            pitems.append(item("memory", "MEMORY.md", "project", mem, min(len(read_text(mem).encode()), 25_000), 0,
                               scope_path=sp))
        nested = 0
        for dirpath, dirnames, filenames in os.walk(P):
            depth = len(Path(dirpath).parts) - len(P.parts)
            dirnames[:] = [d for d in dirnames if d not in cfg.get("exclude_dirs", []) and not d.startswith(".")]
            if depth >= 4:
                dirnames[:] = []
            if depth > 0 and "CLAUDE.md" in filenames:
                f = Path(dirpath) / "CLAUDE.md"
                pitems.append(item("claude_md", str(f.relative_to(P)), "project", f, 0, len(read_text(f).encode()),
                                   scope_path=sp, conditional=True, note="loaded when Claude reads files in that folder"))
                nested += 1
        items += pitems
        projects.append({"path": sp, "abs": str(P), "items": [i["id"] for i in pitems], "ancestor_claude_md": ancestors,
                         "has_git": (P / ".git").exists()})
    return items, projects, {"settings": settings, "claude_json_projects": len(cj.get("projects") or {}),
                             "hooks": sorted((settings.get("hooks") or {}).keys()),
                             "enabled_plugins": enabled_plugins}


# ---------------------------------------------------------------- MCP schema probe (stdio only)

def probe_stdio(conf: dict, timeout: int, version: str) -> dict:
    cmd = [conf["command"]] + [str(a) for a in conf.get("args", [])]
    env = {**os.environ, **{k: str(v) for k, v in (conf.get("env") or {}).items()}}
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "stellaxis", "version": version}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=env, cwd=conf.get("cwd") or None)
    except OSError as e:
        return {"error": str(e)[:200]}
    out = {"error": "timeout"}
    try:
        proc.stdin.write((json.dumps(msgs[0]) + "\n").encode())
        proc.stdin.flush()
        deadline = time.time() + timeout
        sent_list = False
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("id") == 1 and not sent_list:
                instr = (r.get("result") or {}).get("instructions") or ""
                proc.stdin.write((json.dumps(msgs[1]) + "\n" + json.dumps(msgs[2]) + "\n").encode())
                proc.stdin.flush()
                sent_list = True
            elif r.get("id") == 2:
                tools = (r.get("result") or {}).get("tools") or []
                out = {"tools": len(tools), "schema_bytes": len(json.dumps(tools).encode()),
                       "instructions_bytes": len(instr.encode()), "tool_names": [t.get("name") for t in tools][:200]}
                break
    except OSError as e:
        out = {"error": str(e)[:200]}
    finally:
        try:
            proc.kill()
        except OSError:
            pass
        for pipe in (proc.stdin, proc.stdout):
            try:
                pipe.close()
            except OSError:
                pass
        proc.wait()
    return out


def run_probes(items: list, cfg: dict, cache: dict, status, version: str, force=False) -> dict:
    probes = cache.setdefault("probes", {})
    if not cfg.get("probe_mcp"):
        return probes
    max_age = parse_duration(cfg.get("probe_every", "24h"), 86400)
    for it in items:
        conf = it.get("_conf")
        if it["kind"] != "mcp" or not conf or not conf.get("command"):
            continue
        if it["scope"] == "project":  # a cloned repo's .mcp.json is not trusted until Claude Code asks; never run it here
            continue
        h = it["conf_hash"]
        if not force and h in probes and time.time() - probes[h].get("at", 0) < max_age:
            continue
        status("probing MCP server", server=it["name"], scope=it["scope"])
        res = probe_stdio(conf, int(cfg.get("probe_timeout", 20)), version)
        res["at"] = time.time()
        probes[h] = res
    return probes

