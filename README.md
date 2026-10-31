# Trade Reconciliation

A **Claude Agent SDK orchestrator** that reconciles monthly brokerage statements against client
trades and advisor records: extracts statements (Claude), compares trades against metrics
(deterministic Python), pulls client<->advisor notes from Salesforce, builds an Excel/PDF
suspicious-activity report, emails compliance, and — once the compliance officer replies —
closes the loop by marking the account's record as reviewed (approved/rejected).

See [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) for the full component and sequence diagrams,
and [docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md) for setup. This replaces an earlier version built
on the Microsoft Agent Framework and Microsoft Foundry Local.

Trade comparison is deterministic Python, never recomputed by the model; Claude is used for
statement extraction (forced structured output), summarizing Salesforce notes, and classifying
compliance officer replies.

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
npm install -g @anthropic-ai/claude-code   # the Agent SDK drives this CLI
cp .env.example .env                       # fill in ANTHROPIC_API_KEY at minimum
```
`ANTHROPIC_API_KEY` is required. Email (Graph: `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`,
`GRAPH_CLIENT_SECRET`, `GRAPH_SENDER`, `EMAIL_TO`) and Salesforce (`SF_DOMAIN`, `SF_CLIENT_ID`,
`SF_CLIENT_SECRET`) are optional — without them, runs still work (dry run, no notes section).
See [docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md) for the Entra and Salesforce connected-app setup.

## Run
Folder layout: `<folder>/statements/*.pdf`, `<folder>/client_trades/*.xlsx`,
and `<folder>/master/*.xlsx`.
```bash
.venv/bin/python cli.py run --folder ./samples --dry-run
.venv/bin/python cli.py run --folder ./samples
```
The first command runs the full orchestrator and builds reports without sending email. The
second sends the compliance email if suspicious pricing is detected. Updated trackers and
reports are written under `output/` unless `--in-place` is used.

### Closing the compliance loop
```bash
.venv/bin/python cli.py watch-compliance --folder ./samples
```
Polls the configured mailbox for replies to a `[Case: <id>]` report email, classifies
approve/reject/needs-info, and writes the decision into `output/compliance_ledger.sqlite3` and
the master tracker's `Compliance Status` / `Reviewed By` / `Reviewed At` columns. Add `--once` to
process pending replies a single time instead of polling.

### Dashboard
```bash
scripts/run_dashboard.sh
```
Live transcript of the orchestrator, metrics, discrepancies, reports, run history, and a
Compliance tab showing every case's status. See [docs/dashboard.md](./docs/dashboard.md).

## Rules
Trades match on (symbol, trade date, side). Flagged: quantity or account differences; price when
BUY and client price > advisor price, or SELL and client price < advisor price; trades present on one side only.
Matching and comparison are deterministic code. PDFs must have a text layer;
scanned PDFs currently fail extraction and are excluded from the comparison.

## Tests
`.venv/bin/python -m pytest`

Tests exercise the deterministic modules (`compare.py`, `excel_io.py`, `report.py`, `ledger.py`)
directly and the Claude-backed functions (`extraction.py`, `compliance_watch.py`) with a stub
Anthropic client — no live API key, Salesforce, or Graph credentials required to run the suite.
