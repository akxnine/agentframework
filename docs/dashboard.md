# Interactive Dashboard

A Streamlit GUI to trigger the reconciliation, watch each step live, and explore metrics, discrepancies and reports.
The CLI (`main.py`) is unchanged and keeps working.

## Prerequisites

- Python 3.10+
- Microsoft Foundry Local installed (`foundry` on PATH) with a model downloaded (`foundry model download <name>`).
  See [FOUNDRY_DEPLOYMENT.md](../FOUNDRY_DEPLOYMENT.md).
- Optional `.env` for email (`GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, `GRAPH_SENDER`, `EMAIL_TO`).
  Not needed for dry runs.

## 1. Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 2. Run

```bash
scripts/run_dashboard.sh            # http://localhost:8501
scripts/run_dashboard.sh 9000       # custom port
```

Equivalent manual command:

```bash
.venv/bin/python -m streamlit run dashboard_app.py
```

The script uses `.venv` when present (otherwise `python3`) and installs missing dependencies.

## 3. Use it

1. Open http://localhost:8501.
2. In the sidebar set the **Docs folder** (must contain `statements/`, `client_trades/`, `master/`; default `./samples`).
3. Choose options:
   - **Dry run**: build reports but do not send email (default on).
   - **Update master trackers in place**: otherwise copies go to `output/master`.
   - **Keep Foundry server running**: leave the server up after the run.
4. Click **▶ Run reconciliation**. If the Foundry server is not ready, the pre-check runs
   `precheck/foundry_preflight.py` to start it and load a model. **⏹ Cancel** stops the run at the next await point
   (a PDF already inside an LLM call finishes first).

### Tabs

| Tab | Shows |
|-----|-------|
| 🔴 Live | Six-step stepper with timings, per-PDF progress bar, KPI cards, live log |
| 📈 Metrics | Per-PDF pages, tokens, retries, read/LLM time, with charts |
| ⚠️ Discrepancies | Filter by kind/account, PRICE rows highlighted, CSV export |
| 📄 Reports | Download generated suspicious-activity Excel/PDF |
| 🕘 History | All past runs and their totals |

Use **Run to view** in the sidebar to open a past run in every tab.

## How live progress works

`recon/events.py` appends JSON events (`step`, `progress`, `doc`, `metrics`, `discrepancies`, `log`, `run_start`,
`run_end`) to `output/runs/<run_id>.jsonl`. `recon/runner.py` runs the pipeline in a background thread and the
dashboard re-reads the file about once per second while a run is active. Because events are on disk, a browser
refresh does not lose progress and past runs stay viewable. Delete `output/runs/` to clear history.

## Debugging

### VS Code (recommended)

Open the repo and pick a configuration from **Run and Debug** (defined in [.vscode/launch.json](../.vscode/launch.json)):

- **Debug: Dashboard (Streamlit)**: launches the dashboard under the debugger; set breakpoints in
  `dashboard_app.py`, `recon/runner.py` or `recon/pipeline.py`.
- **Debug: CLI pipeline (dry run)**: runs `main.py --folder ./samples --dry-run --keep-server`.

### Attach from a terminal

```bash
scripts/debug_dashboard.sh          # waits on localhost:5678, dashboard on 8501
scripts/debug_dashboard.sh 9000 5679
```

Then start **Attach: Dashboard (debugpy)** in VS Code. The dashboard loads once the debugger attaches.
`debugpy` is installed automatically if missing.

Note: the pipeline runs in a worker thread, so breakpoints in `recon/` code hit on that thread; Streamlit reruns
`dashboard_app.py` on every interaction, so breakpoints there fire repeatedly.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Run fails immediately with "Pre-check failed" | Run `python precheck/foundry_preflight.py` for details; check `foundry` is installed and a model is cached |
| "A run is already in progress" | Wait or press Cancel; only one run at a time per dashboard process |
| Port in use | `scripts/run_dashboard.sh 8502` |
| No data in Metrics/Discrepancies | Select a finished run in **Run to view** |
| Stop the dashboard | `Ctrl+C` in its terminal |
