# Trade Reconciliation

Python CLI that extracts trades from brokerage statement PDFs with a local
Foundry model, compares them with client trade workbooks, updates master
trackers, and creates and emails reports when suspicious pricing is found.
Trade comparison is deterministic; the model is used for statement extraction.

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```
Install the Foundry Local CLI, then cache `phi-4-mini` (preferred) and/or `gemma-4-e2b-it` with
`foundry model download <name>`; the preflight uses the first one that is cached. Configure `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`,
`GRAPH_CLIENT_SECRET`, `GRAPH_SENDER`, and `EMAIL_TO` in the environment or a
local `.env` file. Graph email requires an Entra app with **Mail.Send**
application permission and admin consent.

## Run
Folder layout: `<folder>/statements/*.pdf`, `<folder>/client_trades/*.xlsx`,
and `<folder>/master/*.xlsx`.
```bash
.venv/bin/python foundry_preflight.py --run -- --folder ./samples --dry-run
.venv/bin/python foundry_preflight.py --run -- --folder ./samples
```
The first command verifies the local model and creates reports without sending
email. The second sends email if suspicious pricing is detected. Updated
trackers and reports are written under `output/` unless `--in-place` is used.

## Rules
Trades match on (symbol, trade date, side). Flagged: quantity or account differences; price when
BUY and client price > advisor price, or SELL and client price < advisor price; trades present on one side only.
Matching and comparison are deterministic code. PDFs must have a text layer;
scanned PDFs currently fail extraction and are excluded from the comparison.

## Tests
`.venv/bin/python -m pytest`

## Microsoft Foundry deployment
See [FOUNDRY_DEPLOYMENT.md](./FOUNDRY_DEPLOYMENT.md) for deployment prerequisites,
required application changes, and hosted-agent deployment steps.
