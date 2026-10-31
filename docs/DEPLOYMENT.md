# Deployment: Claude Agent SDK orchestrator

Replaces the previous Microsoft Foundry Local deployment (there is no local model server to
install, start, or keep patched). The orchestrator calls the Anthropic API through the Claude
Agent SDK instead.

## 1. Runtime prerequisites

- **Python 3.10+**
- **Node.js 18+** — the Python Agent SDK drives the `@anthropic-ai/claude-code` CLI as a
  subprocess. Install it once per host/image:
  ```bash
  npm install -g @anthropic-ai/claude-code
  ```
  Verify with `claude --version`. If it isn't on `PATH` in your deployment image, set
  `CLAUDE_CODE_CLI_PATH` to its absolute path (read by `orchestrator/config.py`).
- **ANTHROPIC_API_KEY** — an API key from the Anthropic Console, or a Bedrock/Vertex-backed
  Claude Code auth profile if your org routes model traffic through AWS/GCP.

## 2. Python dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 3. Microsoft Graph (compliance email)

Entra app registration with **application** permissions:
- `Mail.Send` — to email the suspicious-activity report to compliance.
- `Mail.Read` — new requirement, to poll the sender's inbox for approve/reject replies.

Grant admin consent, then set:
```
GRAPH_TENANT_ID=...
GRAPH_CLIENT_ID=...
GRAPH_CLIENT_SECRET=...
GRAPH_SENDER=reconciliation@yourfirm.com   # mailbox the app sends/reads as
EMAIL_TO=compliance@yourfirm.com,other@yourfirm.com
```

## 4. Salesforce (client/advisor notes)

Create a connected app with the OAuth2 client-credentials flow enabled (or use a dedicated
integration user with username/password + security token). Grant read access to `Account`,
`Case`, `CaseComment`, `Task`, `EmailMessage`. Set:
```
SF_DOMAIN=https://yourfirm.my.salesforce.com
SF_CLIENT_ID=...
SF_CLIENT_SECRET=...
# Only if using username/password instead of pure client-credentials:
SF_USERNAME=...
SF_PASSWORD=...
SF_SECURITY_TOKEN=...
```
The brokerage account number from each statement is matched against a custom field on
`Account` — `Account_Number__c` by default; override with `SF_ACCOUNT_NUMBER_FIELD` if your org
uses a different field name. If Salesforce isn't configured, the pipeline still runs; the
Salesforce-notes section of the report is simply omitted.

## 5. Running

```bash
# One-off run, builds reports but does not email (safe to try first)
python cli.py run --folder ./samples --dry-run

# Real run: emails compliance if suspicious pricing is found
python cli.py run --folder ./samples

# Separate, long-running process: closes the compliance loop
python cli.py watch-compliance --folder ./samples
```

Run `watch-compliance` as its own process (systemd unit, container sidecar, scheduled Lambda,
etc.) — it polls the configured mailbox every `COMPLIANCE_POLL_SECONDS` (default 60) and updates
`output/compliance_ledger.sqlite3` plus the master tracker workbooks as replies arrive. It does
not need to run on the same host as `cli.py run`, only against the same `output/` and `master/`
locations (or an equivalent shared/mounted path if you containerize each process separately).

## 6. Containerizing

A minimal image needs: Python 3.10+, Node 18+ with the Claude Code CLI installed globally, the
repo's `requirements.txt` installed, and the environment variables above injected as secrets
(not baked into the image). Mount or persist `output/` (reports, the compliance ledger, and the
event log the dashboard reads) across restarts.

## 7. Observability

- `output/runs/<run_id>.jsonl` — the full event stream for one run (tool calls, tool results,
  the orchestrator's narration, final cost/turn count). Read by `dashboard_app.py`; also readable
  directly for log shipping.
- `ResultMessage.total_cost_usd` / `model_usage` (emitted as the `agent_result` event) are
  Anthropic's own client-side cost estimate for the run — treat as an estimate, not a bill.
