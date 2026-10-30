# Monthly Statement Reconciliation Workflow

Monthly brokerage statements (PDF) are reconciled against the client trades exported from Arbor, and the
MasterPro account tracker is updated. Barcode splitting is **not** part of this flow: every PDF in the input folder
is processed as it arrives. A **Claude Agent SDK orchestrator** (see
[docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)) drives every stage below by calling deterministic Python tools
in order; it does not recompute any number a tool returns.

## 1. Intake
1. Monthly brokerage statements arrive as PDFs from various firms and are placed in the statements folder.
2. Download the client trades Excel from Arbor into the client trades folder.

## 2. Extract (to structured data)
3. For each PDF, read the text layer. A PDF with no text layer (scanned) is reported as needing OCR.
4. A single forced-schema Claude call (`extract_statement` tool) extracts, per account found in the PDF:
   - broker/platform name
   - account number and account type
   - account holder and related persons
   - trade transactions: date, symbol, BUY/SELL, quantity, price

## 3. Match and compare
5. Look up each statement account in the MasterPro tracker (client and advisor links).
6. Match advisor trades from the statement to client trades in the Arbor Excel on
   account + symbol + date + side (`compare_and_flag` tool, deterministic).
7. Flag deviations:
   - **Suspicious:** the client SELL price is below the advisor SELL price.
   - **Suspicious:** the client BUY price is above the advisor BUY price.
   - **Other discrepancies:** quantity differs, account differs, or a trade is missing on one side.

## 4. Update the tracker
8. For each account on a statement, write these MasterPro tracker columns:
   - **Review Date**
   - **Number of Trades**
   - **Suspicious Activity** (Yes/No)

## 5. Pull client<->advisor context from Salesforce
9. For every account with suspicious (PRICE) activity, fetch Cases, CaseComments, Tasks, and EmailMessages
   tied to that account from Salesforce and have the `compliance-analyst` subagent summarize them into a
   short narrative for the compliance report.

## 6. Report, notify, and track the compliance decision
10. If any suspicious activity was found, generate the Suspicious Activity Report (Excel + PDF, including
    the Salesforce notes section), open a `PENDING` compliance case per flagged account in the ledger, and
    email it to compliance with a `[Case: <id>]` token per account in the subject. If not, only the tracker
    is updated and no email is sent.
11. A separate watcher process polls the mailbox for the compliance officer's reply, classifies it as
    `APPROVED` / `REJECTED` / `NEEDS_INFO`, and writes that decision back into the ledger and the tracker
    workbook (**Compliance Status**, **Reviewed By**, **Reviewed At**, **Compliance Notes** columns) — this
    is how a document gets marked reviewed once compliance responds.
12. Every step is logged to the terminal and streamed as events the dashboard renders live.

```
Statement PDFs (statements/)        Arbor client trades Excel (client_trades/)
        |                                   |
   extract (Claude) ------------+-----------+
                                v
          match via MasterPro tracker (master/), compare prices (deterministic)
                                |
                +----------------+----------------+
          no suspicious activity          suspicious activity
                |                                |
      update tracker only     update tracker + Salesforce notes + SAR (Excel + PDF)
                                   + email compliance with [Case: ...] token
                                                |
                                   compliance watcher polls mailbox
                                                |
                                   reply classified -> ledger + tracker updated
                                       (Compliance Status = APPROVED/REJECTED/NEEDS_INFO)
```

## Running it

```
python make_samples.py                       # sample data in ./samples
python cli.py run --folder samples --dry-run  # builds reports, does not email
python cli.py run --folder samples            # emails compliance if suspicious pricing is found
python cli.py watch-compliance --folder samples  # separate process: closes the approval loop
```

Folder layout under `--folder`: `statements/*.pdf`, `client_trades/*.xlsx`, `master/*.xlsx`.
Updated trackers are written to `output/master/` (inputs are left untouched) unless `--in-place` is given.

## Data formats

**Client trades (Arbor)** columns: `Account Number, Client Name, Symbol, Trade Date, Side, Quantity, Price`.

**MasterPro tracker** columns: `Account Number, Client Name, Advisor, Broker, Account Type, Review Date,
Number of Trades, Suspicious Activity` (filled/created by `run`), plus `Compliance Status, Reviewed By,
Reviewed At, Compliance Notes` (filled/created by `watch-compliance` once a decision arrives).
