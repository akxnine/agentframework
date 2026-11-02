import glob
import json
import logging
import os
import time

import pymupdf
import anthropic

from .config import Settings
from .console import ExtractStats
from .dashboard import LLMCall, llm_calls
from .models import Statement, StatementList

log = logging.getLogger("recon")

PROMPT = """Extract every account statement from the brokerage document below. A document may contain several accounts.
Return JSON of this shape:
{"statements": [{"broker": "brokerage firm name", "account_number": "...", "account_type": "e.g. Individual, IRA, Joint, Trust",
 "account_holder": "primary holder name", "related_persons": ["joint owner, beneficiary, trustee, advisor names"],
 "trades": [{"symbol": "TICKER", "trade_date": "YYYY-MM-DD", "side": "BUY or SELL", "quantity": 0, "price": 0}]}]}
Rules: one entry per account; trades are executed buy/sell transactions only (skip dividends, fees, totals, balances);
price is per unit; quantity is a positive number; do not invent data."""


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


async def _extract(settings: Settings, path: str, attempts: int, stats: ExtractStats,
                   client: anthropic.AsyncAnthropic | None = None) -> list[Statement]:
    t_read = time.time()
    text = read_pdf_text(path, stats)
    stats.read_secs = time.time() - t_read
    client = client or anthropic.AsyncAnthropic()
    last: Exception | None = None
    name = os.path.basename(path)
    for attempt in range(1, attempts + 1):
        t0 = time.time()
        stats.attempts = attempt
        # Structured outputs constrain the reply to the StatementList schema, so it is always valid JSON.
        resp = await client.messages.parse(
            model=settings.model, max_tokens=settings.max_tokens,
            output_config={"effort": settings.effort},
            output_format=StatementList,
            messages=[{"role": "user", "content": f"{PROMPT}\n\nDocument text:\n{text}"}])
        usage = resp.usage
        call = LLMCall(f"{name} (try {attempt}/{attempts})", "Extract accounts & trades from PDF text",
                       usage.input_tokens or 0, usage.output_tokens or 0, time.time() - t0)
        llm_calls.append(call)
        stats.prompt_tokens += call.prompt_tokens
        stats.completion_tokens += call.completion_tokens
        stats.llm_secs += time.time() - t0
        data = resp.parsed_output
        if data is None:  # refusal or truncation (max_tokens): no usable output
            call.ok = False
            last = ValueError(f"no structured output (stop_reason={resp.stop_reason})")
            log.warning("  attempt %d/%d returned no structured output: %s", attempt, attempts, resp.stop_reason)
            continue
        found = _normalise(data.statements, name)
        stats.status, stats.statements = "OK", len(found)
        stats.trades = sum(len(x.trades) for x in found)
        stats.accounts = [x.account_number for x in found]
        return found
    raise ValueError(f"could not parse model output: {last}")


def save_json(statements: list[Statement], out_dir: str, pdf_path: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(pdf_path))[0] + ".json")
    with open(out, "w") as f:
        json.dump([s.model_dump() for s in statements], f, indent=2)
    return out
