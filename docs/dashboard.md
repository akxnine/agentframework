# Interactive Dashboard

A Streamlit GUI to trigger the reconciliation, watch the Claude Agent SDK orchestrator work in
real time, and explore metrics, discrepancies, reports, and compliance case status.
`cli.py` is unchanged and keeps working alongside it.

## Prerequisites

- Python 3.10+ and Node.js 18+ (the Agent SDK drives the Claude Code CLI as a subprocess).
- `ANTHROPIC_API_KEY` set in the environment or a local `.env` file.
- Optional for email + compliance replies: `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`,
  `GRAPH_CLIENT_SECRET`, `GRAPH_SENDER`, `EMAIL_TO`. Not needed for dry runs.
- Optional for the Salesforce notes section: `SF_DOMAIN`, `SF_CLIENT_ID`, `SF_CLIENT_SECRET`
  (and `SF_USERNAME`/`SF_PASSWORD`/`SF_SECURITY_TOKEN` if not using client-credentials).

See [DEPLOYMENT.md](./DEPLOYMENT.md) for full setup steps.

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

## 3. Use it

1. Open http://localhost:8501.
2. In the sidebar set the **Docs folder** (must contain `statements/`, `client_trades/`,
   `master/`; default `./samples`), then **Run reconciliation**.
3. **Live** tab: the orchestrator's transcript streams in as it extracts statements, compares
   trades, pulls Salesforce notes, and (if not a dry run) emails compliance.
4. **Metrics** tab: token usage and estimated cost for the Claude calls this run made.
5. **Discrepancies** tab: filter and export the flagged trades.
6. **Reports** tab: download the Excel/PDF reports from any past run.
7. **History** tab: every run so far, with status and cost.
8. **Compliance** tab: every case opened for compliance review and its current status
   (`PENDING`/`APPROVED`/`REJECTED`/`NEEDS_INFO`). Use **Check mailbox for replies now** to poll
   immediately instead of waiting for the background watcher
   (`python cli.py watch-compliance`).
