#!/usr/bin/env bash
# Stellaxis installer for macOS.
#   bash collector/install.sh              # service + desktop MCP + first snapshot
#   bash collector/install.sh --no-service # skip the launchd collector
#   bash collector/install.sh --no-desktop # skip Claude desktop MCP registration
#   bash collector/install.sh --uninstall  # remove service and desktop entry (keeps ~/.stellaxis data)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STX="${STELLAXIS_HOME:-$HOME/.stellaxis}"
PY="$(command -v python3)"
LABEL="com.stellaxis.collector"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DESKTOP_CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
SERVICE=1; DESKTOP=1; UNINSTALL=0
for a in "$@"; do
  case "$a" in
    --no-service) SERVICE=0 ;;
    --no-desktop) DESKTOP=0 ;;
    --uninstall) UNINSTALL=1 ;;
    *) echo "unknown option $a"; exit 2 ;;
  esac
done

desktop_edit() { # $1 = add|remove
  [ -f "$DESKTOP_CFG" ] || { [ "$1" = add ] && mkdir -p "$(dirname "$DESKTOP_CFG")" && echo '{}' > "$DESKTOP_CFG"; } || return 0
  cp "$DESKTOP_CFG" "$DESKTOP_CFG.stellaxis-backup"
  "$PY" - "$1" "$DESKTOP_CFG" "$PY" "$REPO/collector/mcp_server.py" <<'EOF'
import json, sys
op, path, py, server = sys.argv[1:]
cfg = json.load(open(path)) if open(path).read().strip() else {}
servers = cfg.setdefault("mcpServers", {})
if op == "add":
    servers["stellaxis"] = {"command": py, "args": [server]}
else:
    servers.pop("stellaxis", None)
json.dump(cfg, open(path, "w"), indent=2)
EOF
}

if [ "$UNINSTALL" = 1 ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  desktop_edit remove
  rm -f "$STX/bin"
  echo "Removed the service and the desktop MCP entry. Data kept in $STX."
  exit 0
fi

mkdir -p "$STX"
ln -sfn "$REPO/collector" "$STX/bin"
[ -f "$STX/config.json" ] || cat > "$STX/config.json" <<'EOF'
{
  "interval": "1m",
  "port": 7777,
  "scan_roots": ["~"],
  "scan_depth": 6
}
EOF
echo "Config: $STX/config.json (edit interval, scan_roots, pricing; the service re-reads it every cycle)"

echo "Collecting the first snapshot (probing stdio MCP servers)..."
"$PY" "$REPO/collector/collect.py" run --probe

if [ "$SERVICE" = 1 ]; then
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$REPO/collector/collect.py</string><string>serve</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>$STX/serve.log</string>
  <key>StandardErrorPath</key><string>$STX/serve.log</string>
</dict></plist>
EOF
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "Service running: http://127.0.0.1:$("$PY" -c "import json;print(json.load(open('$STX/config.json')).get('port',7777))")"
fi

if [ "$DESKTOP" = 1 ]; then
  desktop_edit add
  echo "Registered the stellaxis MCP server in the Claude desktop app. Restart the app to load it."
fi

cat <<EOF

Next:
  1. Plugin (skill + agent) for Claude Code:
       claude plugin marketplace add "$REPO"
       claude plugin install stellaxis@stellaxis
  2. Ask Claude: "use stellaxis-auditor to audit my setup".
  3. macOS may ask to let python3 read folders such as Documents; allow it so repos there are found,
     or narrow "scan_roots" in $STX/config.json.
EOF
