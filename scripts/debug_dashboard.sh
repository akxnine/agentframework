#!/usr/bin/env bash
# Start the dashboard under debugpy and wait for a debugger to attach.
# Usage: scripts/debug_dashboard.sh [port] [debug_port]
# Then attach with the "Attach: Dashboard (debugpy)" configuration in VS Code.
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-${DASHBOARD_PORT:-8501}}"
DEBUG_PORT="${2:-${DEBUG_PORT:-5678}}"
PY=".venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

"$PY" -c "import streamlit, plotly, pandas" 2>/dev/null || "$PY" -m pip install -q -r requirements.txt
"$PY" -c "import debugpy" 2>/dev/null || "$PY" -m pip install -q debugpy

echo "Waiting for debugger on localhost:${DEBUG_PORT} (dashboard will be at http://localhost:${PORT}) ..."
exec "$PY" -m debugpy --listen "$DEBUG_PORT" --wait-for-client \
  -m streamlit run dashboard_app.py --server.port "$PORT" --server.headless true
