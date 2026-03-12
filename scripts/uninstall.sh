#!/usr/bin/env bash
set -euo pipefail

APP_HOME="$HOME/.local/share/openclaw-desktop"
LAUNCHER="$HOME/.local/bin/openclaw-desktop"

rm -rf "$APP_HOME"
rm -f "$LAUNCHER"

echo "Uninstalled OpenClaw Desktop."
