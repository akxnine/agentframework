"""Statement extraction: PDF text -> structured Statement[].

Uses the Anthropic Messages API directly (not the agentic tool-use loop) with a
single forced tool call, so the model's reply is guaranteed to be a JSON object
matching StatementList and never free-form chat. This backs the `extract_statement`
SDK tool in orchestrator/tools.py; it has no claude_agent_sdk dependency itself,
so it can be unit-tested with a stub Anthropic client.
"""
import glob
import json
import logging
import os
import time

import pymupdf
from anthropic import AsyncAnthropic

from .config import Settings
from .console import ExtractStats
from .dashboard import LLMCall, llm_calls
from .models import Statement, StatementList

log = logging.getLogger("recon")

INSTRUCTIONS = """Extract every account statement from the brokerage document below. A document may contain several accounts.
Rules: one entry per account; trades are executed buy/sell transactions only (skip dividends, fees, totals, balances);
price is per unit; quantity is a positive number; do not invent data."""

TOOL_NAME = "record_statements"
TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": "Record the accounts and trades found in a brokerage statement.",
    "input_schema": {
        "type": "object",
        "properties": {
            "statements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "broker": {"type": "string", "description": "brokerage firm name"},
                        "account_number": {"type": "string"},
                        "account_type": {"type": "string", "description": "e.g. Individual, IRA, Joint, Trust"},
                        "account_holder": {"type": "string", "description": "primary holder name"},
                        "related_persons": {"type": "array", "items": {"type": "string"},
                                            "description": "joint owner, beneficiary, trustee, advisor names"},
                        "trades": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "symbol": {"type": "string"},
                                    "trade_date": {"type": "string", "description": "YYYY-MM-DD"},
                                    "side": {"type": "string", "enum": ["BUY", "SELL"]},
                                    "quantity": {"type": "number"},
                                    "price": {"type": "number"},
                                },
                                "required": ["symbol", "trade_date", "side", "quantity", "price"],
                            },
                        },
                    },
                    "required": ["broker", "account_number", "trades"],
                },
            },
        },
        "required": ["statements"],
    },
}


def list_pdfs(folder: str) -> list[str]:
    return sorted(glob.glob(os.path.join(folder, "*.pdf")))


def read_pdf_text(path: str, stats: ExtractStats | None = None) -> str:
    with pymupdf.open(path) as doc:
        if stats:
            stats.pages = len(doc)
        text = "\n\n".join(page.get_text().strip() for page in doc)
    if not text.strip():
        raise ValueError("no text layer (scanned PDF); OCR is not configured")
    if stats:
        stats.chars = len(text)
    return text


def _normalise(statements: list[Statement], name: str) -> list[Statement]:
    for st in statements:
        st.source_file = name
        st.account_number = st.account_number.strip()
        for t in st.trades:
            t.source_file = name
            t.account = st.account_number
            t.symbol = t.symbol.strip().upper()
    return statements


async def extract_statements(settings: Settings, path: str, attempts: int = 2,
                             stats: ExtractStats | None = None) -> list[Statement]:
    stats = stats or ExtractStats(os.path.basename(path))
    t_start = time.time()
    try:
        return await _extract(settings, path, attempts, stats)
    finally:
        stats.total_secs = time.time() - t_start


async def _extract(settings: Settings, path: str, attempts: int, stats: ExtractStats) -> list[Statement]:
    t_read = time.time()
    text = read_pdf_text(path, stats)
    stats.read_secs = time.time() - t_read
    settings.require_anthropic_env()
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    name = os.path.basename(path)
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        t0 = time.time()
        stats.attempts = attempt
        statements, usage = await extract_statement(client, settings.model, text)
        call = LLMCall(f"{name} (try {attempt}/{attempts})", "Extract accounts & trades from PDF text",
                       usage.get("input_tokens", 0), usage.get("output_tokens", 0), time.time() - t0)
        llm_calls.append(call)
        stats.prompt_tokens += call.prompt_tokens
        stats.completion_tokens += call.completion_tokens
        stats.llm_secs += time.time() - t0
        if statements is not None:
            found = _normalise(statements, name)
            stats.status, stats.statements = "OK", len(found)
            stats.trades = sum(len(x.trades) for x in found)
            stats.accounts = [x.account_number for x in found]
            return found
        call.ok = False
        last = ValueError("model did not return a valid record_statements tool call")
        log.warning("  attempt %d/%d returned invalid output", attempt, attempts)
    raise ValueError(f"could not parse model output: {last}")


async def extract_statement(client: AsyncAnthropic, model: str, document_text: str) -> tuple[list[Statement] | None, dict]:
    """One forced tool-use call: returns (statements, token usage) or (None, usage) if the model
    declined to produce a valid tool call. Kept separate from _extract so it can be unit-tested
    directly with a stub client and reused by the `extract_statement` SDK tool."""
    resp = await client.messages.create(
        model=model, max_tokens=4096, temperature=0,
        tools=[TOOL_SCHEMA], tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": f"{INSTRUCTIONS}\n\nDocument text:\n{document_text}"}],
    )
    usage = {"input_tokens": getattr(resp.usage, "input_tokens", 0) or 0,
             "output_tokens": getattr(resp.usage, "output_tokens", 0) or 0}
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
            try:
                return StatementList.model_validate(block.input).statements, usage
            except Exception:  # noqa: BLE001 - pydantic.ValidationError or malformed input
                return None, usage
    return None, usage


def save_json(statements: list[Statement], out_dir: str, pdf_path: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(pdf_path))[0] + ".json")
    with open(out, "w") as f:
        json.dump([s.model_dump() for s in statements], f, indent=2)
    return out
