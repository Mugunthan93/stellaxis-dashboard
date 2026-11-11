from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

HOME = Path.home()


def read_json(p: Path, default=None):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(p: Path, data, indent=None, ensure_ascii=True):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, default=list, ensure_ascii=ensure_ascii)
    os.replace(tmp, p)


def short_path(p) -> str:
    s = str(p)
    h = str(HOME)
    return "~" + s[len(h):] if s == h or s.startswith(h + os.sep) else s


def parse_duration(s, default=60) -> int:
    if isinstance(s, (int, float)):
        return int(s)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", str(s or ""))
    if not m:
        return default
    return int(float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)])


def now() -> dt.datetime:
    """The one clock (UTC, aware). STELLAXIS_NOW fixes it, for tests."""
    fixed = os.environ.get("STELLAXIS_NOW")
    if fixed:
        return dt.datetime.fromisoformat(fixed.replace("Z", "+00:00"))
    return dt.datetime.now(dt.timezone.utc)


def now_iso() -> str:
    return now().strftime("%Y-%m-%dT%H:%M:%SZ")
