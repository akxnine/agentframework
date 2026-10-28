# Trade Reconciliation Agent

Orchestration agent built on Microsoft Agent Framework (Python) with a local Ollama model (`gemma4:12b`, vision-capable).
It reads client and advisor PDFs (text or scanned), extracts trades, compares them, and if discrepancies exist
creates an Excel report and emails it via Microsoft Graph.

## Setup
```bash
brew install ollama && ollama serve        # model gemma4:12b must be pulled (ollama pull gemma4:12b)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                       # fill in Graph + recipient settings
```
Graph: register an Entra app with the **Mail.Send** application permission (admin consent); set tenant/client/secret and `GRAPH_SENDER`.

## Run
Folder layout: `<folder>/client/*.pdf` and `<folder>/advisor/*.pdf`.
```bash
.venv/bin/python make_samples.py                        # optional sample data (one scanned PDF)
.venv/bin/python main.py --folder ./samples --dry-run   # Excel only, no email
.venv/bin/python main.py --folder ./samples             # sends email if discrepancies
```

## Rules
Trades match on (symbol, trade date, side). Flagged: quantity or account differences; price when
BUY and client price > advisor price, or SELL and client price < advisor price; trades present on one side only.
Matching/comparison is deterministic code; the LLM only extracts and orchestrates.
Scanned pages (no text layer) are rendered to images and read by Gemma4 vision. Extraction is slow on CPU/small GPUs.

## Tests
`.venv/bin/python -m pytest`
