import glob
import logging
import os
from datetime import date

from .compare import compare_trades
from .config import Settings
from .dashboard import DocRow, Summary, llm_calls, print_dashboards
from .emailer import send_email
from .excel_io import norm_acct, read_client_trades, tracker_accounts, update_tracker
from .extraction import extract_statements, list_pdfs, save_json
from .models import Discrepancy, Statement, Trade
from .report import build_excel, build_pdf

log = logging.getLogger("recon")


def compare_by_account(client: list[Trade], advisor: list[Trade]) -> list[Discrepancy]:
    """Run the trade comparison separately for every account so trades never cross accounts."""
    out: list[Discrepancy] = []
    for acct in sorted({norm_acct(t.account) for t in advisor}):
        # accounts are already matched, so give each trade the same account text (formatting may differ)
        c = [t.model_copy(update={"account": acct}) for t in client if norm_acct(t.account) == acct]
        a = [t.model_copy(update={"account": acct}) for t in advisor if norm_acct(t.account) == acct]
        label = next(t.account for t in advisor if norm_acct(t.account) == acct)
        for d in compare_trades(c, a):
            d.account = label
            out.append(d)
    return out


async def run_pipeline(settings: Settings, dry_run: bool = False) -> str:
    log.info("== 0. Pre-check: %s ==", settings.check_server())
    out_dir = settings.output_folder
    llm_calls.clear()
    summary = Summary()

    log.info("== 1. Extract statements ==")
    statements: list[Statement] = []
    failed: list[str] = []
    pdfs = list_pdfs(settings.statements_dir)
    for path in pdfs:
        log.info("Extracting %s", path)
        try:
            found = await extract_statements(settings, path)
        except Exception as e:
            log.error("  failed: %s", e)
            failed.append(f"{os.path.basename(path)}: {e}")
            summary.docs.append(DocRow(os.path.basename(path), error=str(e).splitlines()[0][:60]))
            continue
        summary.docs.append(DocRow(os.path.basename(path), len(found), sum(len(s.trades) for s in found)))
        for s in found:
            log.info("  %s | account %s (%s) | holder %s | related %s | %d trades", s.broker, s.account_number,
                     s.account_type or "-", s.account_holder or "-", ", ".join(s.related_persons) or "-", len(s.trades))
        log.info("  saved %s", save_json(found, os.path.join(out_dir, "json"), path))
        statements.extend(found)
    if not pdfs:
        return f"No PDF statements found in {settings.statements_dir}"
    if failed:
        log.warning("%d statement file(s) failed extraction and are excluded", len(failed))

    log.info("== 2. Load Arbor client trades ==")
    client_trades: list[Trade] = []
    for path in sorted(glob.glob(os.path.join(settings.client_trades_dir, "*.xlsx"))):
        trades = read_client_trades(path)
        log.info("  %s: %d trades", os.path.basename(path), len(trades))
        client_trades.extend(trades)

    log.info("== 3. Compare ==")
    advisor_trades = [t for s in statements for t in s.trades]
    discrepancies = compare_by_account(client_trades, advisor_trades)
    suspicious = [d for d in discrepancies if d.kind == "PRICE"]
    for d in discrepancies:
        log.info("  [%s] %s %s %s %s: %s", "SUSPICIOUS" if d.kind == "PRICE" else d.kind, d.account, d.side,
                 d.symbol, d.trade_date, d.detail)
    log.info("  %d discrepancies, %d suspicious", len(discrepancies), len(suspicious))

    log.info("== 4. Update MasterPro tracker ==")
    results = {norm_acct(s.account_number): (len(s.trades), any(norm_acct(d.account) == norm_acct(s.account_number)
                                                                for d in suspicious)) for s in statements}
    known: set[str] = set()
    for path in sorted(glob.glob(os.path.join(settings.master_dir, "*.xlsx"))):
        target = path if settings.in_place else os.path.join(out_dir, "master", os.path.basename(path))
        accounts = update_tracker(path, target, results, date.today().isoformat())
        known |= tracker_accounts(path)
        log.info("  %s -> %s (%d accounts updated)", os.path.basename(path), target, len(accounts))
    for acct in sorted(set(results) - known):
        log.warning("  statement account %s not found in any tracker", acct)

    summary.statements, summary.client_trades, summary.discrepancies = statements, client_trades, discrepancies
    log.info("Dashboard saved to %s", print_dashboards(summary, out_dir))

    lines = [f"{len(statements)} statements ({len(pdfs) - len(failed)}/{len(pdfs)} PDFs), "
             f"{len(advisor_trades)} advisor / {len(client_trades)} client trades, "
             f"{len(discrepancies)} discrepancies, {len(suspicious)} suspicious."]
    lines += [f"Failed: {f}" for f in failed]
    if not suspicious:
        return "\n".join(lines + ["No suspicious activity: no report or email created."])

    log.info("== 5. Suspicious Activity Report ==")
    excel = build_excel(discrepancies, out_dir, len(client_trades), len(advisor_trades))
    pdf = build_pdf(discrepancies, out_dir, len(client_trades), len(advisor_trades))
    lines += [f"Excel: {excel}", f"PDF: {pdf}"]
    if dry_run:
        return "\n".join(lines + ["DRY RUN: email not sent."])
    try:
        status = send_email(settings, f"Suspicious activity report ({len(suspicious)} flagged)",
                            "\n".join(lines) + "\nSee attached Excel and PDF reports.", [excel, pdf])
    except Exception as e:
        status = f"error sending email: {e}"
    return "\n".join(lines + [status])
