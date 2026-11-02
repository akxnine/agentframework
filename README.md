# Trade Reconciliation

Python CLI that extracts trades from brokerage statement PDFs with Claude (via the
[Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python)), compares them with client trade
workbooks, updates master trackers, and creates and emails reports when suspicious pricing is found.
Trade comparison is deterministic; the model is used only for statement extraction (structured outputs, so the
reply always matches the `StatementList` schema).

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # or run `ant auth login`
```
Optional environment variables (or a local `.env`): `CLAUDE_MODEL` (default `claude-opus-5-5`), `CLAUDE_EFFORT`
(`low`..`max`, default `medium`), `CLAUDE_MAX_TOKENS`. For email, configure `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`,
`GRAPH_CLIENT_SECRET`, `GRAPH_SENDER`, and `EMAIL_TO`; Graph email requires an Entra app with **Mail.Send**
application permission and admin consent.

## Run
Folder layout: `<folder>/statements/*.pdf`, `<folder>/client_trades/*.xlsx`, and `<folder>/master/*.xlsx`.
```bash
.venv/bin/python main.py --folder ./samples --dry-run     # build reports, do not send email
.venv/bin/python main.py --folder ./samples               # sends email if suspicious pricing is detected
.venv/bin/python main.py --folder ./samples --model claude-sonnet-5-5
```
Updated trackers and reports are written under `output/` unless `--in-place` is used.
A dashboard is available with `scripts/run_dashboard.sh` (see [docs/dashboard.md](docs/dashboard.md)).

## Rules
Trades match on (symbol, trade date, side). Flagged: quantity or account differences; price when
BUY and client price > advisor price, or SELL and client price < advisor price; trades present on one side only.
Matching and comparison are deterministic code. PDFs must have a text layer;
scanned PDFs currently fail extraction and are excluded from the comparison.

## Tests
`.venv/bin/python -m pytest` (no API key needed; the Claude client is faked in tests).
