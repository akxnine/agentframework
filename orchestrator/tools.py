"""Custom tools exposed to the orchestrator agent as an in-process MCP server.

Every tool wraps plain, deterministic or narrowly-scoped Python (compare.py, excel_io.py,
report.py, salesforce_client.py, graph_client.py, ledger.py, extraction.py). The orchestrator
agent's system prompt (see agents.py) treats whatever a tool returns as ground truth.
"""
import glob
import os
from datetime import date
from typing import Any

from anthropic import AsyncAnthropic
from claude_agent_sdk import create_sdk_mcp_server, tool

from . import compare, excel_io, extraction, graph_client, ledger as ledger_mod, report
from .config import Settings
from .events import emit
from .models import Discrepancy, Trade

SERVER_NAME = "recon"


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    import json
    return {"content": [{"type": "text", "text": json.dumps(data, default=str)}]}


def build_mcp_server(settings: Settings):
    """Build the in-process MCP server for one pipeline run. A fresh server is built per run
    so the Anthropic client, Salesforce session, and ledger handle are scoped to that run."""
    anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None
    compliance_ledger = ledger_mod.ComplianceLedger(settings.ledger_path)
    _sf_session: dict[str, Any] = {}

    def _salesforce():
        if "client" not in _sf_session:
            from . import salesforce_client
            _sf_session["client"] = salesforce_client.connect(settings)
        return _sf_session["client"]

    @tool("list_input_documents", "List statement PDFs, client-trade workbooks, and master tracker "
         "workbooks found under the configured docs folder.", {})
    async def list_input_documents(_args: dict) -> dict:
        return _ok({
            "statements": sorted(glob.glob(os.path.join(settings.statements_dir, "*.pdf"))),
            "client_trades": sorted(glob.glob(os.path.join(settings.client_trades_dir, "*.xlsx"))),
            "master_trackers": sorted(glob.glob(os.path.join(settings.master_dir, "*.xlsx"))),
        })

    @tool("extract_statement", "Extract every account and trade from one brokerage statement PDF. "
         "Uses a single forced-schema Claude call so the result always validates.", {"pdf_path": str})
    async def extract_statement(args: dict) -> dict:
        if anthropic_client is None:
            return _ok({"error": "ANTHROPIC_API_KEY not configured"})
        text = extraction.read_pdf_text(args["pdf_path"])
        statements, usage = await extraction.extract_statement(anthropic_client, settings.model, text)
        name = os.path.basename(args["pdf_path"])
        if statements is None:
            emit("doc", file=name, statements=0, trades=0, status="FAILED", error="model returned invalid output",
                prompt_tokens=usage.get("input_tokens", 0), completion_tokens=usage.get("output_tokens", 0))
            return _ok({"error": "model did not return valid statements", "usage": usage})
        for st in statements:
            st.source_file = name
            for t in st.trades:
                t.source_file, t.account = name, st.account_number
        emit("doc", file=name, statements=len(statements), trades=sum(len(s.trades) for s in statements),
            status="OK", error="", prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            accounts=[s.account_number for s in statements])
        return _ok({"statements": [s.model_dump() for s in statements], "usage": usage})

    @tool("load_client_trades", "Load trades from one Arbor client-trade workbook (.xlsx).",
         {"xlsx_path": str})
    async def load_client_trades(args: dict) -> dict:
        trades = excel_io.read_client_trades(args["xlsx_path"])
        emit("client_file", file=os.path.basename(args["xlsx_path"]), trades=len(trades))
        return _ok({"trades": [t.model_dump() for t in trades]})

    @tool("compare_and_flag", "Deterministically compare client trades against advisor (statement) "
         "trades, grouped by account. Returns every discrepancy found; never recompute these numbers "
         "yourself, they are the ground truth.",
         {"client_trades": list, "advisor_trades": list})
    async def compare_and_flag(args: dict) -> dict:
        client = [Trade.model_validate(t) for t in args["client_trades"]]
        advisor = [Trade.model_validate(t) for t in args["advisor_trades"]]
        discrepancies = compare.compare_by_account(client, advisor)
        suspicious = [d for d in discrepancies if d.kind == "PRICE"]
        emit("discrepancies", rows=[d.model_dump() for d in discrepancies])
        emit("metrics", advisor_trades=len(advisor), client_trades=len(client),
            discrepancies=len(discrepancies), suspicious=len(suspicious))
        return _ok({"discrepancies": [d.model_dump() for d in discrepancies],
                   "total": len(discrepancies), "suspicious": len(suspicious)})

    @tool("update_master_trackers", "Write Review Date / Number of Trades / Suspicious Activity into "
         "every MasterPro tracker workbook for the given accounts.",
         {"results": dict, "in_place": bool})
    async def update_master_trackers(args: dict) -> dict:
        results = {k: (v[0], v[1]) for k, v in args["results"].items()}
        updated, known = {}, set()
        for path in sorted(glob.glob(os.path.join(settings.master_dir, "*.xlsx"))):
            target = path if args.get("in_place") else os.path.join(settings.output_folder, "master", os.path.basename(path))
            accounts = excel_io.update_tracker(path, target, results, date.today().isoformat())
            known |= excel_io.tracker_accounts(path)
            updated[os.path.basename(path)] = {"target": target, "accounts": accounts}
            emit("tracker", file=os.path.basename(path), target=target, accounts_updated=len(accounts))
        unmatched = sorted(set(results) - known)
        return _ok({"updated": updated, "unmatched_accounts": unmatched})

    @tool("fetch_salesforce_notes", "Fetch client<->advisor notes and comments from Salesforce "
         "(Case, CaseComment, Task, EmailMessage) for one brokerage account number. Returns an "
         "empty list, not an error, if Salesforce is unavailable or the account isn't linked.",
         {"account_number": str})
    async def fetch_salesforce_notes(args: dict) -> dict:
        missing = settings.require_salesforce_env()
        if missing:
            return _ok({"notes": [], "warning": f"Salesforce not configured: missing {', '.join(missing)}"})
        from . import salesforce_client
        try:
            notes = salesforce_client.fetch_notes_for_account(_salesforce(), args["account_number"])
        except Exception as e:  # noqa: BLE001 - surface as data, don't fail the run over Salesforce
            return _ok({"notes": [], "warning": f"Salesforce query failed: {e}"})
        return _ok({"notes": [n.model_dump() for n in notes]})

    @tool("build_reports", "Build the Excel and PDF suspicious-activity report from the discrepancies "
         "found, optionally with a Salesforce-notes section per account.",
         {"discrepancies": list, "n_client_trades": int, "n_advisor_trades": int, "notes_by_account": dict})
    async def build_reports(args: dict) -> dict:
        discrepancies = [Discrepancy.model_validate(d) for d in args["discrepancies"]]
        out_dir = settings.output_folder
        notes = args.get("notes_by_account") or None
        excel_path = report.build_excel(discrepancies, out_dir, args["n_client_trades"], args["n_advisor_trades"], notes)
        pdf_path = report.build_pdf(discrepancies, out_dir, args["n_client_trades"], args["n_advisor_trades"], notes)
        emit("reports", excel=excel_path, pdf=pdf_path)
        return _ok({"excel_path": excel_path, "pdf_path": pdf_path})

    @tool("send_compliance_email", "Email the compliance report (Excel + PDF attachments) via Microsoft "
         "Graph. The subject MUST include a [Case: <id>] token for every case referenced so replies "
         "can be matched back automatically.",
         {"subject": str, "body": str, "attachment_paths": list})
    async def send_compliance_email(args: dict) -> dict:
        missing = settings.require_email_env()
        if missing:
            return _ok({"sent": False, "warning": f"Email not configured: missing {', '.join(missing)}"})
        status = graph_client.send_email(settings, args["subject"], args["body"], args["attachment_paths"])
        return _ok({"sent": True, "status": status})

    @tool("record_compliance_case", "Create a PENDING compliance case in the ledger for one flagged "
         "account from this run, so the compliance-watcher can later match an email reply to it.",
         {"run_id": str, "account": str, "report_paths": list, "discrepancy_count": int, "suspicious_count": int})
    async def record_compliance_case(args: dict) -> dict:
        case = compliance_ledger.create_case(args["run_id"], args["account"], args["report_paths"],
                                             args["discrepancy_count"], args["suspicious_count"])
        emit("compliance_case", case_id=case.case_id, account=case.account, status=case.status)
        return _ok({"case_id": case.case_id, "status": case.status})

    tools = [list_input_documents, extract_statement, load_client_trades, compare_and_flag,
            update_master_trackers, fetch_salesforce_notes, build_reports, send_compliance_email,
            record_compliance_case]
    return create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=tools)


def allowed_tool_names() -> list[str]:
    names = ["list_input_documents", "extract_statement", "load_client_trades", "compare_and_flag",
            "update_master_trackers", "fetch_salesforce_notes", "build_reports", "send_compliance_email",
            "record_compliance_case"]
    return [f"mcp__{SERVER_NAME}__{n}" for n in names]
