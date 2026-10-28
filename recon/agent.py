import json
import os
from typing import Annotated

from agent_framework import Agent, tool
from agent_framework_ollama import OllamaChatClient

from .compare import compare_trades
from .config import Settings
from .emailer import send_email as _send_email
from .extraction import extract_trades as _extract, list_pdfs
from .models import Discrepancy, Trade
from .report import build_excel

INSTRUCTIONS = """You orchestrate trade reconciliation. Always follow these steps in order:
1. Call list_documents to see the client and advisor PDFs.
2. Call extract_trades once per PDF (role is 'client' or 'advisor').
3. Call compare_trades. If it reports 0 discrepancies, stop and say everything matches (do NOT create Excel or email).
4. Otherwise call create_excel_report, then send_email_report with the returned path.
Finish with a short summary: counts, discrepancy breakdown, report path, email status. Never make up numbers."""


class State:
    def __init__(self):
        self.client: list[Trade] = []
        self.advisor: list[Trade] = []
        self.discrepancies: list[Discrepancy] = []
        self.report_path = ""


def build_agent(settings: Settings, dry_run: bool = False) -> tuple[Agent, State]:
    st = State()

    @tool
    def list_documents() -> str:
        """List client and advisor PDF files in the configured folder."""
        return json.dumps({r: [os.path.basename(p) for p in list_pdfs(settings.docs_folder, r)]
                           for r in ("client", "advisor")})

    @tool
    async def extract_trades(role: Annotated[str, "'client' or 'advisor'"],
                             filename: Annotated[str, "PDF file name from list_documents"]) -> str:
        """Extract trades from one PDF (text or scanned) using the local vision model."""
        if role not in ("client", "advisor"):
            return "error: role must be 'client' or 'advisor'"
        path = os.path.join(settings.docs_folder, role, os.path.basename(filename))
        if not os.path.exists(path):
            return f"error: {filename} not found"
        trades = await _extract(settings, path)
        getattr(st, role).extend(trades)
        return f"Extracted {len(trades)} trades from {filename}"

    @tool
    def compare_trades_tool() -> str:
        """Compare all extracted client trades with advisor trades and report discrepancies."""
        st.discrepancies = compare_trades(st.client, st.advisor)
        counts: dict[str, int] = {}
        for d in st.discrepancies:
            counts[d.kind] = counts.get(d.kind, 0) + 1
        return json.dumps({"client_trades": len(st.client), "advisor_trades": len(st.advisor),
                           "discrepancies": len(st.discrepancies), "by_kind": counts})

    compare_trades_tool.name = "compare_trades"

    @tool
    def create_excel_report() -> str:
        """Create the Excel discrepancy report and return its file path."""
        if not st.discrepancies:
            return "error: no discrepancies"
        st.report_path = build_excel(st.discrepancies, settings.output_folder, len(st.client), len(st.advisor))
        return st.report_path

    @tool
    def send_email_report(path: Annotated[str, "Path returned by create_excel_report"]) -> str:
        """Email the Excel report via Microsoft Graph."""
        if dry_run:
            return f"DRY RUN: email not sent; report at {path}"
        body = (f"Trade reconciliation found {len(st.discrepancies)} discrepancies between client and advisor "
                f"records ({len(st.client)} client / {len(st.advisor)} advisor trades). See attached report.")
        try:
            return _send_email(settings, f"Trade discrepancies ({len(st.discrepancies)})", body, st.report_path or path)
        except Exception as e:
            return f"error sending email: {e}"

    client = OllamaChatClient(host=settings.ollama_host, model=settings.model)
    agent = Agent(client, INSTRUCTIONS, name="TradeReconciliationAgent",
                  tools=[list_documents, extract_trades, compare_trades_tool, create_excel_report, send_email_report])
    return agent, st
