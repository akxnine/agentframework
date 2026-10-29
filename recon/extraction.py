import glob
import json
import logging
import os
import time

import pymupdf
from openai import AsyncOpenAI

from .config import Settings
from .dashboard import LLMCall, llm_calls
from .models import Statement, StatementList

log = logging.getLogger("recon")

PROMPT = """Extract every account statement from the brokerage document below. A document may contain several accounts.
Return ONLY JSON of this shape:
{"statements": [{"broker": "brokerage firm name", "account_number": "...", "account_type": "e.g. Individual, IRA, Joint, Trust",
 "account_holder": "primary holder name", "related_persons": ["joint owner, beneficiary, trustee, advisor names"],
 "trades": [{"symbol": "TICKER", "trade_date": "YYYY-MM-DD", "side": "BUY or SELL", "quantity": 0, "price": 0}]}]}
Rules: one entry per account; trades are executed buy/sell transactions only (skip dividends, fees, totals, balances);
price is per unit; quantity is a positive number; do not invent data."""


def list_pdfs(folder: str) -> list[str]:
    return sorted(glob.glob(os.path.join(folder, "*.pdf")))


def read_pdf_text(path: str) -> str:
    with pymupdf.open(path) as doc:
        text = "\n\n".join(page.get_text().strip() for page in doc)
    if not text.strip():
        raise ValueError("no text layer (scanned PDF); OCR is not configured")
    return text


def _json_block(text: str) -> str:
    """Strip markdown fences / prose around the JSON object the model returns."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"model returned no JSON: {text[:200]!r}")
    return text[start:end + 1]


def _normalise(statements: list[Statement], name: str) -> list[Statement]:
    for st in statements:
        st.source_file = name
        st.account_number = st.account_number.strip()
        for t in st.trades:
            t.source_file = name
            t.account = st.account_number
            t.symbol = t.symbol.strip().upper()
    return statements


async def extract_statements(settings: Settings, path: str, attempts: int = 2) -> list[Statement]:
    text = read_pdf_text(path)
    client = AsyncOpenAI(base_url=settings.openai_base_url, api_key="foundry-local")
    last: Exception | None = None
    name = os.path.basename(path)
    for attempt in range(1, attempts + 1):
        t0 = time.time()
        resp = await client.chat.completions.create(
            model=settings.model, temperature=0,
            messages=[{"role": "user", "content": f"{PROMPT}\n\nDocument text:\n{text}"}])
        usage = resp.usage
        call = LLMCall(f"{name} (try {attempt}/{attempts})", "Extract accounts & trades from PDF text",
                       getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0,
                       time.time() - t0)
        llm_calls.append(call)
        try:
            data = StatementList.model_validate_json(_json_block(resp.choices[0].message.content or ""))
            return _normalise(data.statements, name)
        except ValueError as e:  # includes pydantic.ValidationError
            call.ok = False
            last = e
            log.warning("  attempt %d/%d returned invalid JSON: %s", attempt, attempts, str(e).splitlines()[0])
    raise ValueError(f"could not parse model output: {last}")


def save_json(statements: list[Statement], out_dir: str, pdf_path: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(pdf_path))[0] + ".json")
    with open(out, "w") as f:
        json.dump([s.model_dump() for s in statements], f, indent=2)
    return out
