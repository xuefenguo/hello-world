#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_HOME="$HOME/.local/share/openclaw-desktop"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/openclaw-desktop"

mkdir -p "$APP_HOME" "$BIN_DIR"
cp -r "$ROOT_DIR/apps" "$APP_HOME/"
cp -r "$ROOT_DIR/scripts" "$APP_HOME/"
cp "$ROOT_DIR/README.md" "$APP_HOME/"

cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
python3 "$APP_HOME/apps/orchestrator/server.py"
EOF
chmod +x "$LAUNCHER"

echo "Installed OpenClaw Desktop."
echo "Run: $LAUNCHER"
echo "Then open: http://127.0.0.1:8765/"
