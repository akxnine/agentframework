# Architecture: Claude Agent SDK Orchestrator

This replaces the previous design (Microsoft Agent Framework + Microsoft Foundry
Local for statement extraction) with an **agent orchestrator built on the
[Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/python)**. The
orchestrator coordinates specialized Claude agents and a set of deterministic
Python tools to run the monthly trade-reconciliation workflow end to end:
scan statements and client trades, compare them against advisor records and
tolerance metrics, pull Salesforce notes/comments for every flagged account,
generate Excel/PDF reports, email compliance, and close the loop when
compliance replies with a decision.

## Design principle: deterministic math, agentic judgment

Financial comparison logic must be reproducible and auditable, so it is never
delegated to an LLM. The split is:

| Concern | Owner | Why |
|---|---|---|
| Trade matching, price/qty/account comparison, tracker math | **Deterministic Python** (`orchestrator/compare.py`, `excel_io.py`) | Same input must always give the same output; must be unit-testable without network/API access |
| Reading unstructured PDF text into structured statements | **Claude, forced tool-use** (`extract_statement` tool, `orchestrator/extraction.py`) | Statement layouts vary by broker; this is a language-understanding task, but the output must validate against the `StatementList` schema every time, so the call forces a single structured tool response rather than free chat |
| Summarizing Salesforce notes/comments into a compliance-readable narrative | **Claude** (`compliance-analyst` subagent) | Free-text notes need synthesis, not computation |
| Classifying a compliance officer's free-text email reply (approve / reject / needs info) | **Claude** (`compliance-analyst` subagent) | Natural-language understanding of an arbitrary reply; same subagent, a different instruction |
| Sequencing the above and deciding what to do next | **Orchestrator agent** (`ClaudeSDKClient` main loop) | Ties the stages together, narrates progress, drafts the compliance email, delegates to `compliance-analyst` when judgment is needed |

The orchestrator's system prompt explicitly instructs Claude to treat every
tool's return value as ground truth and never recompute or restate numbers
differently from what a tool returned. Every deterministic tool
(`orchestrator/compare.py`, `orchestrator/excel_io.py`, `orchestrator/report.py`)
is plain Python with no SDK or network dependency, so it is unit-tested
directly without booting the agent loop or holding an Anthropic API key.

## Components

```mermaid
flowchart TB
    subgraph Inputs
        PDF[Broker statement PDFs]
        CT[Arbor client-trade workbooks .xlsx]
        MT[MasterPro tracker workbooks .xlsx]
    end

    subgraph Orchestrator["Orchestrator (Claude Agent SDK)"]
        direction TB
        MAIN["Orchestrator agent\n(ClaudeSDKClient main loop,\nsystem prompt = run contract)"]
        SA1["Subagent: compliance-analyst\n(note summarization + reply classification)"]
        MAIN -. delegates via Task tool .-> SA1
    end

    subgraph Tools["In-process MCP tools (orchestrator/tools.py)"]
        T1[[extract_statement]]
        T2[[load_client_trades]]
        T3[[compare_and_flag]]
        T4[[update_tracker]]
        T5[[fetch_salesforce_notes]]
        T6[[build_reports]]
        T7[[send_compliance_email]]
        T8[[record_compliance_case]]
    end

    subgraph External["External systems"]
        ANTHROPIC[(Anthropic API)]
        SF[(Salesforce\nCases / Comments / Tasks / EmailMessage)]
        GRAPH[(Microsoft Graph\nmail)]
        LEDGER[(Compliance ledger\nSQLite)]
        OUT[(output/\nExcel + PDF reports,\nupdated trackers)]
    end

    PDF --> T1
    CT --> T2
    MT --> T4
    MAIN --> T1 & T2 & T3 & T4 & T5 & T6 & T7 & T8
    T1 <-->|forced tool-use call| ANTHROPIC
    SA1 <-->|subagent turn| ANTHROPIC
    T5 <--> SF
    MAIN -. summarize notes .-> SA1
    T3 --> T4
    T4 --> OUT
    T6 --> OUT
    T7 <--> GRAPH
    T8 <--> LEDGER

    GRAPH -. compliance reply .-> WATCH
    subgraph Watcher["Compliance watcher (separate process)"]
        WATCH["compliance_watch.py\npolling loop"]
    end
    WATCH -. classify reply .-> SA1
    WATCH --> LEDGER
    WATCH --> OUT
```

## Main run: sequence diagram

```mermaid
sequenceDiagram
    autonumber
    participant CLI as cli.py run
    participant ORCH as Orchestrator agent (Claude)
    participant EXT as extract_statement (tool, forced tool-use)
    participant CMP as compare_and_flag (tool)
    participant SFTOOL as fetch_salesforce_notes (tool)
    participant SFAPI as Salesforce
    participant SFA as compliance-analyst subagent
    participant RPT as build_reports (tool)
    participant MAIL as send_compliance_email (tool)
    participant GRAPH as Microsoft Graph
    participant LEDGER as Compliance ledger

    CLI->>ORCH: run(docs_folder, thresholds)
    ORCH->>EXT: extract_statement(pdf_text) for each PDF
    EXT-->>ORCH: Statement JSON (broker, account, trades)
    ORCH->>CMP: compare_and_flag(client_trades, advisor_trades)
    CMP-->>ORCH: Discrepancy[] (deterministic)
    alt suspicious discrepancies found
        ORCH->>SFTOOL: fetch_salesforce_notes(account)
        SFTOOL->>SFAPI: SOQL: Case, CaseComment, Task, EmailMessage WHERE AccountId=...
        SFAPI-->>SFTOOL: records
        SFTOOL-->>ORCH: raw notes per account
        ORCH->>SFA: delegate (Task tool): summarize notes for flagged accounts
        SFA-->>ORCH: narrative notes per account
        ORCH->>RPT: build_reports(discrepancies, notes)
        RPT-->>ORCH: excel_path, pdf_path
        ORCH->>LEDGER: record_compliance_case(case_id, account, PENDING)
        ORCH->>MAIL: send_compliance_email(subject="[Case: <id>] ...", attachments)
        MAIL->>GRAPH: POST /sendMail
    else no suspicious discrepancies
        ORCH->>CLI: "No suspicious activity" (no email sent)
    end
    ORCH-->>CLI: run summary
```

## Compliance approval loop: sequence diagram

```mermaid
sequenceDiagram
    autonumber
    participant OFFICER as Compliance officer
    participant GRAPH as Microsoft Graph (mailbox)
    participant WATCH as compliance_watch.py (poller)
    participant CLS as compliance-analyst subagent (Claude)
    participant LEDGER as Compliance ledger (SQLite)
    participant XLS as MasterPro tracker (.xlsx)

    OFFICER->>GRAPH: Reply to "[Case: RECON-...-A1234] Suspicious activity report"
    loop every poll interval
        WATCH->>GRAPH: GET /mailFolders/Inbox/messages?$filter=isRead eq false
        GRAPH-->>WATCH: unread messages
    end
    WATCH->>WATCH: extract case_id from subject via [Case: ...] token
    alt reply body is a clear keyword ("approve"/"reject" as first word)
        WATCH->>WATCH: classify deterministically
    else free-text reply
        WATCH->>CLS: classify(reply_body)
        CLS-->>WATCH: decision=APPROVED|REJECTED|NEEDS_INFO, notes
    end
    WATCH->>LEDGER: update_case(case_id, status, reviewer, notes, reviewed_at)
    WATCH->>XLS: update_tracker row: Compliance Status, Reviewed By, Reviewed At
    WATCH->>GRAPH: mark message read + send acknowledgement reply
```

## Data model

- `Trade`, `Statement`, `Discrepancy` — unchanged semantics from the previous
  deterministic pipeline (`orchestrator/models.py`).
- `SalesforceNote` — `{source, author, created_at, body}` normalized from
  `CaseComment`, `Task`, and `EmailMessage` records.
- `ComplianceCase` — `{case_id, run_id, account, status, created_at,
  reviewed_at, reviewer_email, decision_notes, report_paths, suspicious_count}`.
  `status ∈ {PENDING, APPROVED, REJECTED, NEEDS_INFO}`. Persisted in
  `output/compliance_ledger.sqlite3` (`orchestrator/ledger.py`), one row per
  flagged account per run, keyed by `case_id = RECON-<run_id>-<account>`.

## Why a case ID per account, not per run

A single email can cover several flagged accounts. Embedding the case ID in
the email subject (`[Case: RECON-20261030-143501-A1234]`) and keeping Graph's
`conversationId` lets compliance approve accounts independently and lets the
watcher match a reply to the exact case even if the subject line is edited by
a mail client, since the token is matched by regex anywhere in the subject.

## Deployment prerequisites

- Python 3.10+, Node.js 18+ (the Agent SDK drives the `@anthropic-ai/claude-code`
  CLI as a subprocess — `npm install -g @anthropic-ai/claude-code`).
- `ANTHROPIC_API_KEY` (or a Bedrock/Vertex-backed Claude Code auth profile).
- Entra app registration with Graph `Mail.Send` + `Mail.Read` application
  permissions (read is new — needed to poll for compliance replies).
- Salesforce connected app (OAuth2 client-credentials or JWT bearer) with read
  access to Account, Case, CaseComment, Task, EmailMessage.

See [DEPLOYMENT.md](./DEPLOYMENT.md) for step-by-step setup and
[dashboard.md](./dashboard.md) for the Streamlit GUI.
