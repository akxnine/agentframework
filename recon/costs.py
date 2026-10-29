"""Token pricing and input scanning for the dashboard."""
import glob
import os

import httpx

# USD per 1M tokens (input, output). Approximate public list prices for what-if comparison; edit as needed.
# The local Foundry model runs on your own hardware, so it is free per token.
PRICING: dict[str, tuple[float, float]] = {
    "Local (Foundry)": (0.0, 0.0),
    "Claude Sonnet 4.5": (3.0, 15.0),
    "Claude Haiku 4.5": (1.0, 5.0),
    "GPT-4o": (2.5, 10.0),
    "GPT-4o mini": (0.15, 0.60),
}
LOCAL = "Local (Foundry)"


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
            import fitz  # pymupdf
            with fitz.open(p) as d:
                pages = d.page_count
        except Exception:  # unreadable PDF is reported as pages=None
            pass
        docs.append({"file": os.path.basename(p), "pages": pages, "size_kb": round(os.path.getsize(p) / 1024, 1)})

    host = os.getenv("FOUNDRY_HOST", "").rstrip("/")
    model = os.getenv("FOUNDRY_MODEL", "")
    server_ok = False
    if host:
        try:
            r = httpx.get(f"{host}/v1/models", timeout=2)
            server_ok = model in [m.get("id") for m in r.json().get("data", [])]
        except (httpx.HTTPError, ValueError):
            pass
    email_cfg = all(os.getenv(k) for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
                                           "GRAPH_SENDER", "EMAIL_TO"))
    checks = [
        ("Statements (PDF)", bool(pdfs), f"{len(pdfs)} found"),
        ("Client trades (xlsx)", bool(client), f"{len(client)} found"),
        ("Master trackers (xlsx)", bool(master), f"{len(master)} found"),
        ("Foundry model env", bool(host and model), f"{model or 'FOUNDRY_MODEL not set'}"),
        ("Foundry server serving model", server_ok, "ready" if server_ok else "not ready (pre-check will try to start it)"),
        ("Email settings", email_cfg, "configured" if email_cfg else "missing (OK for dry run)"),
    ]
    return {"docs": docs, "client_files": [os.path.basename(p) for p in client],
            "master_files": [os.path.basename(p) for p in master], "checks": checks,
            "blocking_ok": bool(pdfs) and bool(client) and bool(master)}
