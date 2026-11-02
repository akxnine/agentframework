"""Token pricing and input scanning for the dashboard."""
import glob
import os

from .config import DEFAULT_MODEL

# USD per 1M tokens (input, output): Anthropic list prices. Edit as needed for what-if comparison.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}


def cost(prompt_tokens: float, completion_tokens: float, pricing: str) -> float:
    pin, pout = PRICING.get(pricing, (0.0, 0.0))
    return prompt_tokens * pin / 1e6 + completion_tokens * pout / 1e6


def scan_inputs(docs_folder: str) -> dict:
    """Inventory of what a run would process, plus the readiness checks shown before starting."""
    pdfs = sorted(glob.glob(os.path.join(docs_folder, "statements", "*.pdf")))
    client = sorted(glob.glob(os.path.join(docs_folder, "client_trades", "*.xlsx")))
    master = sorted(glob.glob(os.path.join(docs_folder, "master", "*.xlsx")))
    docs = []
    for p in pdfs:
        pages = None
        try:
            import fitz  # pymupdf
            with fitz.open(p) as d:
                pages = d.page_count
        except Exception:  # unreadable PDF is reported as pages=None
            pass
        docs.append({"file": os.path.basename(p), "pages": pages, "size_kb": round(os.path.getsize(p) / 1024, 1)})

    model = os.getenv("CLAUDE_MODEL", DEFAULT_MODEL)
    has_key = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
    email_cfg = all(os.getenv(k) for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
                                           "GRAPH_SENDER", "EMAIL_TO"))
    checks = [
        ("Statements (PDF)", bool(pdfs), f"{len(pdfs)} found"),
        ("Client trades (xlsx)", bool(client), f"{len(client)} found"),
        ("Master trackers (xlsx)", bool(master), f"{len(master)} found"),
        ("Claude credentials", has_key, "ANTHROPIC_API_KEY set" if has_key else "ANTHROPIC_API_KEY not set (or use `ant auth login`)"),
        ("Claude model", True, model),
        ("Email settings", email_cfg, "configured" if email_cfg else "missing (OK for dry run)"),
    ]
    return {"docs": docs, "client_files": [os.path.basename(p) for p in client],
            "master_files": [os.path.basename(p) for p in master], "checks": checks,
            "blocking_ok": bool(pdfs) and bool(client) and bool(master)}
