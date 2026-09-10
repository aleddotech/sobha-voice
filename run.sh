#!/usr/bin/env bash
# ─────────────────────────────────────────────────
#  Sobha Multilingual Voice Agent — Web Server
# ─────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="$SCRIPT_DIR/src"

echo ""
echo "============================================================"
echo "  Sobha Multilingual Voice Agent (Web Portal)"
echo "  Opening http://localhost:8765"
echo "============================================================"
echo ""

# Run inside the sobha conda environment
conda run -n sobha --no-capture-output python "$SRC_DIR/server.py" "$@"
