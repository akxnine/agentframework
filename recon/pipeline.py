import glob
import logging
import os
from dataclasses import asdict
from datetime import date

from .compare import compare_trades
from .config import Settings
from .console import ExtractStats, console, extraction_table, step
from .dashboard import DocRow, Summary, llm_calls, print_dashboards
from .emailer import send_email
from .events import emit
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
    step(0, "Pre-check")
    log.info("[green]✔[/] Foundry server ready: [bold]%s[/]", settings.check_server())
    out_dir = settings.output_folder
    llm_calls.clear()
    summary = Summary()

    step(1, "Extract statements")
    statements: list[Statement] = []
    failed: list[str] = []
    pdfs = list_pdfs(settings.statements_dir)
    all_stats: list[ExtractStats] = []
    for i, path in enumerate(pdfs, 1):
        stats = ExtractStats(os.path.basename(path))
        all_stats.append(stats)
        emit("progress", step=1, done=i - 1, total=len(pdfs), current=stats.file)
        try:
            with console.status(f"[cyan]Extracting {stats.file} ({i}/{len(pdfs)}) ...[/]", spinner="dots"):
                found = await extract_statements(settings, path, stats=stats)
            log.info("[green]✔[/] %s: %d statement(s), %d trades in %.1fs", stats.file, stats.statements,
                     stats.trades, stats.total_secs)
        except Exception as e:
            stats.status, stats.error = "FAILED", str(e).splitlines()[0][:80]
            log.error("[red]✘ %s failed:[/] %s", stats.file, e)
            failed.append(f"{os.path.basename(path)}: {e}")
            summary.docs.append(DocRow(os.path.basename(path), error=str(e).splitlines()[0][:60]))
            emit("doc", **asdict(stats))
            emit("progress", step=1, done=i, total=len(pdfs), current=stats.file)
            continue
        emit("doc", **asdict(stats))
        emit("progress", step=1, done=i, total=len(pdfs), current=stats.file)
        summary.docs.append(DocRow(os.path.basename(path), len(found), sum(len(s.trades) for s in found)))
        for s in found:
            log.info("  [bold]%s[/] | account [cyan]%s[/] (%s) | holder %s | related %s | [bold]%d[/] trades",
                     s.broker, s.account_number, s.account_type or "-", s.account_holder or "-",
                     ", ".join(s.related_persons) or "-", len(s.trades))
        log.info("  saved [dim]%s[/]", save_json(found, os.path.join(out_dir, "json"), path))
        statements.extend(found)
    if all_stats:
        console.print()
        console.print(extraction_table(all_stats))
    if not pdfs:
        return f"No PDF statements found in {settings.statements_dir}"
    if failed:
        log.warning("%d statement file(s) failed extraction and are excluded", len(failed))

    step(2, "Load Arbor client trades")
    client_trades: list[Trade] = []
    for path in sorted(glob.glob(os.path.join(settings.client_trades_dir, "*.xlsx"))):
        trades = read_client_trades(path)
        log.info("  %s: %d trades", os.path.basename(path), len(trades))
        emit("client_file", file=os.path.basename(path), trades=len(trades))
        client_trades.extend(trades)

    step(3, "Compare")
    advisor_trades = [t for s in statements for t in s.trades]
    discrepancies = compare_by_account(client_trades, advisor_trades)
    suspicious = [d for d in discrepancies if d.kind == "PRICE"]
    for d in discrepancies:
        tag = "[bold white on red] SUSPICIOUS [/]" if d.kind == "PRICE" else f"[yellow]\\[{d.kind}][/]"
        log.info("  %s %s %s %s %s: %s", tag, d.account, d.side, d.symbol, d.trade_date, d.detail)
    emit("discrepancies", rows=[d.model_dump() for d in discrepancies])
    emit("metrics", statements=len(statements), advisor_trades=len(advisor_trades),
         client_trades=len(client_trades), discrepancies=len(discrepancies), suspicious=len(suspicious))
    colour = "red" if suspicious else "green"
    log.info("  %d discrepancies, [bold %s]%d suspicious[/]", len(discrepancies), colour, len(suspicious))

    step(4, "Update MasterPro tracker")
    results = {norm_acct(s.account_number): (len(s.trades), any(norm_acct(d.account) == norm_acct(s.account_number)
                                                                for d in suspicious)) for s in statements}
    known: set[str] = set()
    for path in sorted(glob.glob(os.path.join(settings.master_dir, "*.xlsx"))):
        target = path if settings.in_place else os.path.join(out_dir, "master", os.path.basename(path))
        accounts = update_tracker(path, target, results, date.today().isoformat())
        known |= tracker_accounts(path)
        log.info("  %s -> %s (%d accounts updated)", os.path.basename(path), target, len(accounts))
        emit("tracker", file=os.path.basename(path), target=target, accounts_updated=len(accounts))
    for acct in sorted(set(results) - known):
        log.warning("  [yellow]statement account %s not found in any tracker[/]", acct)

    summary.statements, summary.client_trades, summary.discrepancies = statements, client_trades, discrepancies
    log.info("[green]Dashboard saved to[/] %s", print_dashboards(summary, out_dir))

    lines = [f"{len(statements)} statements ({len(pdfs) - len(failed)}/{len(pdfs)} PDFs), "
             f"{len(advisor_trades)} advisor / {len(client_trades)} client trades, "
             f"{len(discrepancies)} discrepancies, {len(suspicious)} suspicious."]
    lines += [f"Failed: {f}" for f in failed]
    if not suspicious:
        return "\n".join(lines + ["No suspicious activity: no report or email created."])

    step(5, "Suspicious Activity Report")
    excel = build_excel(discrepancies, out_dir, len(client_trades), len(advisor_trades))
    pdf = build_pdf(discrepancies, out_dir, len(client_trades), len(advisor_trades))
    lines += [f"Excel: {excel}", f"PDF: {pdf}"]
    emit("reports", excel=excel, pdf=pdf)
    if dry_run:
        return "\n".join(lines + ["DRY RUN: email not sent."])
    try:
        status = send_email(settings, f"Suspicious activity report ({len(suspicious)} flagged)",
                            "\n".join(lines) + "\nSee attached Excel and PDF reports.", [excel, pdf])
    except Exception as e:
        status = f"error sending email: {e}"
    return "\n".join(lines + [status])
