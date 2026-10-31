#!/usr/bin/env bash
# Start the reconciliation dashboard. Usage: scripts/run_dashboard.sh [port]
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-${DASHBOARD_PORT:-8501}}"
PY=".venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

if ! "$PY" -c "import streamlit, plotly, pandas" 2>/dev/null; then
  echo "Installing dependencies from requirements.txt ..."
  "$PY" -m pip install -q -r requirements.txt
fi

echo "Dashboard: http://localhost:${PORT}"
exec "$PY" -m streamlit run dashboard_app.py --server.port "$PORT" --server.headless true
