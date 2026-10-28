import base64
import glob
import os

import pymupdf
from ollama import AsyncClient

from .config import Settings
from .models import Trade, TradeList

MIN_TEXT_CHARS = 80
PROMPT = (
    "Extract every trade from this document. Return JSON {\"trades\": [...]} where each trade has: "
    "symbol (ticker), trade_date (YYYY-MM-DD), side (BUY or SELL), quantity (number), price (number, per unit), "
    "account (account id/number, empty string if none). Do not invent trades; skip totals and fees."
)


def list_pdfs(folder: str, role: str) -> list[str]:
    return sorted(glob.glob(os.path.join(folder, role, "*.pdf")))


def read_pdf(path: str, dpi: int = 150) -> tuple[str, list[str]]:
    """Return (text, base64 page PNGs). Images only populated for pages lacking a text layer."""
    texts, images = [], []
    with pymupdf.open(path) as doc:
        for page in doc:
            t = page.get_text().strip()
            if len(t) >= MIN_TEXT_CHARS:
                texts.append(t)
            else:
                images.append(base64.b64encode(page.get_pixmap(dpi=dpi).tobytes("png")).decode())
    return "\n\n".join(texts), images


async def extract_trades(settings: Settings, path: str) -> list[Trade]:
    text, images = read_pdf(path)
    content = PROMPT + (f"\n\nDocument text:\n{text}" if text else "")
    msg = {"role": "user", "content": content}
    if images:
        msg["images"] = images
    client = AsyncClient(host=settings.ollama_host)
    resp = await client.chat(model=settings.model, messages=[msg], format=TradeList.model_json_schema(),
                             options={"temperature": 0})
    trades = TradeList.model_validate_json(resp.message.content).trades
    name = os.path.basename(path)
    for t in trades:
        t.source_file = name
        t.symbol = t.symbol.strip().upper()
    return trades
