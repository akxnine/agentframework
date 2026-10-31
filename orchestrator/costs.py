"""Token pricing (for the dashboard's cost estimate) and pre-run input/config scanning."""
import glob
import os

# USD per 1M tokens (input, output). Approximate public list prices; edit as needed.
PRICING: dict[str, tuple[float, float]] = {
    "Claude Sonnet 5.5": (3.0, 15.0),
    "Claude Haiku 5.5": (1.0, 5.0),
    "Claude Opus 5.5": (15.0, 75.0),
    "GPT-4o": (2.5, 10.0),
    "GPT-4o mini": (0.15, 0.60),
}
DEFAULT_PRICING = "Claude Sonnet 5.5"


def cost(prompt_tokens: float, completion_tokens: float, pricing: str) -> float:
    pin, pout = PRICING[pricing]
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
            import pymupdf
            with pymupdf.open(p) as d:
                pages = d.page_count
        except Exception:  # unreadable PDF is reported as pages=None
            pass
        docs.append({"file": os.path.basename(p), "pages": pages, "size_kb": round(os.path.getsize(p) / 1024, 1)})

    anthropic_ok = bool(os.getenv("ANTHROPIC_API_KEY"))
    email_cfg = all(os.getenv(k) for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
                                           "GRAPH_SENDER", "EMAIL_TO"))
    sf_cfg = all(os.getenv(k) for k in ("SF_DOMAIN", "SF_CLIENT_ID", "SF_CLIENT_SECRET"))
    checks = [
        ("Statements (PDF)", bool(pdfs), f"{len(pdfs)} found"),
        ("Client trades (xlsx)", bool(client), f"{len(client)} found"),
        ("Master trackers (xlsx)", bool(master), f"{len(master)} found"),
        ("Anthropic API key", anthropic_ok, "configured" if anthropic_ok else "ANTHROPIC_API_KEY not set"),
        ("Email settings (Graph)", email_cfg, "configured" if email_cfg else "missing (OK for dry run)"),
        ("Salesforce settings", sf_cfg, "configured" if sf_cfg else "missing (notes section will be skipped)"),
    ]
    return {"docs": docs, "client_files": [os.path.basename(p) for p in client],
            "master_files": [os.path.basename(p) for p in master], "checks": checks,
            "blocking_ok": bool(pdfs) and bool(client) and bool(master) and anthropic_ok}
