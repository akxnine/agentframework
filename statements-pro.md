# Monthly Statement Reconciliation Workflow

Monthly brokerage statements (PDF) are reconciled against the client trades exported from Arbor, and the
MasterPro account tracker is updated. Barcode splitting is **not** part of this flow: every PDF in the input folder
is processed as it arrives.

## 1. Intake
1. Monthly brokerage statements arrive as PDFs from various firms and are placed in the statements folder.
2. Download the client trades Excel from Arbor into the client trades folder.

## 2. Extract (to JSON)
3. For each PDF, read the text layer. A PDF with no text layer (scanned) is reported as needing OCR.
4. Extract, per account found in the PDF:
   - broker/platform name
   - account number and account type
   - account holder and related persons
   - trade transactions: date, symbol, BUY/SELL, quantity, price
5. Save one JSON file per PDF (it may contain several accounts) under `output/json/`.

## 3. Match and compare
6. Look up each statement account in the MasterPro tracker (client and advisor links).
7. Match advisor trades from the statement JSON to client trades in the Arbor Excel on
   account + symbol + date + side.
8. Flag deviations:
   - **Suspicious:** the client SELL price is below the advisor SELL price.
   - **Suspicious:** the client BUY price is above the advisor BUY price.
   - **Other discrepancies:** quantity differs, or a trade is missing on one side.

## 4. Update the tracker
9. For each account on a statement, write these MasterPro tracker columns:
   - **Review Date**
   - **Number of Trades**
   - **Suspicious Activity** (Yes/No)

## 5. Report and notify
10. If any suspicious activity was found, generate the Suspicious Activity Report (Excel + PDF) and email it.
    If not, only the tracker is updated.
11. Every step is logged to the terminal, followed by a summary dashboard.

```
Statement PDFs (statements/)        Arbor client trades Excel (client_trades/)
        |                                   |
   extract -> JSON ------------+------------+
                               v
          match via MasterPro tracker (master/), compare prices
                               |
                +--------------+--------------+
          no suspicious activity        suspicious activity
                |                             |
      update tracker only          update tracker + SAR (Excel + PDF) + email
```

## Running it

```
python make_samples.py                                   # sample data in ./samples
python precheck/foundry_preflight.py --run -- --folder samples --dry-run
```

Folder layout under `--folder`: `statements/*.pdf`, `client_trades/*.xlsx`, `master/*.xlsx`.
Updated trackers are written to `output/master/` (inputs are left untouched) unless `--in-place` is given.

## Data formats

**Client trades (Arbor)** columns: `Account Number, Client Name, Symbol, Trade Date, Side, Quantity, Price`.

**MasterPro tracker** columns: `Account Number, Client Name, Advisor, Broker, Account Type, Review Date,
Number of Trades, Suspicious Activity` (the last three are filled/created by the run).
