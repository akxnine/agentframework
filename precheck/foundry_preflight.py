#!/usr/bin/env python3
"""Foundry Local preflight: ensure server + model are ready, test a request, show a dashboard.

Usage:
  python precheck/foundry_preflight.py [--model NAME] [--prompt "..."]   # default: first cached of phi-4-mini, gemma-4-e2b-it
  python precheck/foundry_preflight.py --run [-- main.py args]   # preflight, then run the reconciliation with env set
  eval "$(python precheck/foundry_preflight.py --print-env)"      # export FOUNDRY_HOST / FOUNDRY_MODEL in your shell
Exit code is 0 only if every check passes. FOUNDRY_HOST and FOUNDRY_MODEL are only exported after all checks pass.
"""
import argparse
import json
import logging
import os
import shlex
import subprocess
import sys
import time

import requests
from rich.console import Console, Group
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

# Preference order; the first one that is cached locally is used.
MODEL_PREFERENCE = ["phi-4-mini", "gemma-4-e2b-it"]
console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%H:%M:%S]",
    handlers=[RichHandler(console=console, show_path=False, markup=False)],
)
log = logging.getLogger("foundry")

# Collected for the dashboard: name -> (ok, detail, seconds)
results: dict[str, tuple[bool, str, float]] = {}


def run(*args: str, timeout: int = 600) -> str:
    """Run a foundry CLI command, log it, and return stdout (+stderr)."""
    proc = subprocess.run(["foundry", *args], capture_output=True, text=True, timeout=timeout)
    out = (proc.stdout + proc.stderr).strip()
    for line in out.splitlines():
        log.debug("  | %s", line)
    if proc.returncode != 0:
        log.warning("exit code %s", proc.returncode)
    return out


def step(name: str):
    def deco(fn):
        def wrapper(*a, **kw):
            log.info("== %s ==", name)
            t0 = time.time()
            try:
                value, detail = fn(*a, **kw)
                ok = True
            except Exception as exc:  # noqa: BLE001 - report any failure on the dashboard
                value, detail, ok = None, str(exc), False
                log.error("%s failed: %s", name, exc)
            results[name] = (ok, detail, time.time() - t0)
            (log.info if ok else log.error)("%s -> %s", name, detail)
            return value

        return wrapper

    return deco


def run_json(*args: str, timeout: int = 600) -> dict:
    """Run a foundry command with --output json and return the parsed response."""
    out = run(*args, "--output", "json", timeout=timeout)
    start = out.find("{")
    if start < 0:
        raise RuntimeError(f"no JSON in output of foundry {' '.join(args)}: {out[:200]}")
    data = json.loads(out[start:])
    log.debug("  json: %s", json.dumps(data))
    return data


def server_status() -> dict:
    return run_json("server", "status")


def is_ready(status: dict) -> bool:
    return str(status.get("state", "")).lower() == "ready"


def start_server() -> None:
    """`foundry server start` spawns a daemon that can hold captured pipes open, so don't capture its output."""
    subprocess.run(["foundry", "server", "start"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=120)


@step("1. Server start")
def ensure_server():
    status = server_status()
    if not is_ready(status):
        log.warning("Server state=%s, running: foundry server start", status.get("state"))
        start_server()
        for _ in range(30):
            status = server_status()
            if is_ready(status):
                break
            time.sleep(1)
    if not is_ready(status):
        raise RuntimeError(f"server did not become ready (state={status.get('state')})")
    urls = status.get("webUrls") or []
    if not urls:
        raise RuntimeError("status JSON has no webUrls")
    return urls[0], f"state=ready at {urls[0]}"


def loaded_ids(alias: str) -> list[str]:
    models = run_json("model", "list", "--loaded").get("models", [])
    return [m["id"] for m in models if m.get("alias") == alias]


@step("2. Model cached")
def find_cached(names: list[str]):
    """Run `foundry model list --cached` and return (alias, [cached variant ids]) for the first cached model in `names`."""
    entries = run_json("model", "list", "--cached").get("models", [])
    for name in names:
        match = [m for m in entries if name in (m.get("alias"), m.get("id"))]
        if match:
            break
    else:
        raise RuntimeError(f"none of {', '.join(names)} is cached; run `foundry model download {names[0]}` first")
    alias = match[0]["alias"]
    ids = [m["id"] for m in match]
    return (alias, ids), f"alias={alias}, cached: {', '.join(ids)}"


@step("3. Model load")
def load_model(alias: str, cached_ids: list[str]):
    loaded = loaded_ids(alias)
    if not loaded:
        log.info("running: foundry model load %s (can take a while)", alias)
        out = run_json("model", "load", alias, timeout=900)
        if not out.get("success", False):
            raise RuntimeError(f"model load failed: {out.get('message', out)}")
        loaded = loaded_ids(alias)
    model_id = next((i for i in loaded if i in cached_ids), None) or (loaded or cached_ids)[0]
    return model_id, f"active model = {model_id} (loaded)"


@step("4. API check")
def check_api(base: str, model_id: str):
    r = requests.get(f"{base}/v1/models", timeout=15)
    r.raise_for_status()
    ids = [m["id"] for m in r.json().get("data", [])]
    if model_id not in ids:
        raise RuntimeError(f"{model_id} not served by the API")
    return True, f"{len(ids)} models served, {model_id} available"


@step("5. Test request")
def test_request(base: str, model_id: str, prompt: str):
    t0 = time.time()
    r = requests.post(
        f"{base}/v1/chat/completions",
        json={"model": model_id, "messages": [{"role": "user", "content": prompt}], "max_tokens": 64},
        timeout=300,
    )
    r.raise_for_status()
    body = r.json()
    text = body["choices"][0]["message"]["content"].strip()
    usage = body.get("usage", {})
    latency = time.time() - t0
    log.info("response: %s", text)
    return (text, usage, latency), f"{latency:.1f}s, tokens={usage.get('total_tokens', '?')}"


def usage_table(usage: dict, latency: float) -> Table:
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    total = usage.get("total_tokens", prompt + completion)
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)

    def pct(n):
        return f"{n / total * 100:.1f}%" if total else "-"

    t = Table(title="Token usage", expand=True)
    t.add_column("Category")
    t.add_column("Tokens", justify="right")
    t.add_column("% of total", justify="right")
    t.add_row("Prompt (input)", str(prompt), pct(prompt))
    t.add_row("  of which cached", str(cached), pct(cached))
    t.add_row("  of which uncached", str(prompt - cached), pct(prompt - cached))
    t.add_row("Completion (output)", str(completion), pct(completion))
    t.add_row("  of which reasoning", str(reasoning), pct(reasoning))
    t.add_row("  of which answer", str(completion - reasoning), pct(completion - reasoning))
    t.add_section()
    t.add_row("[bold]Total[/]", f"[bold]{total}[/]", "100.0%" if total else "-")
    if latency > 0:
        t.add_row("Output speed", f"{completion / latency:.1f} tok/s", "")
    return t


def dashboard(url, model_id, reply):
    table = Table(title="Checks", expand=True)
    table.add_column("Step")
    table.add_column("Status", justify="center")
    table.add_column("Detail", overflow="fold")
    table.add_column("Time", justify="right")
    for name, (ok, detail, secs) in results.items():
        table.add_row(name, "[green]PASS[/]" if ok else "[red]FAIL[/]", detail, f"{secs:.1f}s")

    info = Table.grid(padding=(0, 2))
    info.add_row("[bold]Server URL[/]", url or "-")
    info.add_row("[bold]Active model[/]", model_id or "-")
    if reply:
        text, usage, latency = reply
        info.add_row("[bold]Latency[/]", f"{latency:.2f}s")
        info.add_row("[bold]Reply[/]", text)

    parts = [info, table]
    if reply:
        parts.append(usage_table(reply[1], reply[2]))

    all_ok = all(ok for ok, _, _ in results.values()) and len(results) == 5
    title = "[green]FOUNDRY READY[/]" if all_ok else "[red]FOUNDRY NOT READY[/]"
    console.print(Panel(Group(*parts), title=title, border_style="green" if all_ok else "red"))
    return all_ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="use only this model instead of the preference order: " + ", ".join(MODEL_PREFERENCE))
    ap.add_argument("--prompt", default="Reply with one short sentence confirming you are working.")
    ap.add_argument("-v", "--verbose", action="store_true", help="log raw CLI output")
    ap.add_argument("--run", action="store_true", help="after a successful preflight, run main.py with the env set")
    ap.add_argument("--print-env", action="store_true", help="print export lines for the shell (stdout) and exit")
    ap.add_argument("main_args", nargs="*", help="extra arguments for main.py (use after --)")
    args = ap.parse_args()
    if args.verbose:
        log.setLevel(logging.DEBUG)
    if args.print_env:
        # keep stdout clean for eval; logs and dashboard go to stderr
        console.file = sys.stderr
        for h in log.root.handlers:
            h.console = console

    url = model_id = reply = None
    url = ensure_server()
    if url:
        found = find_cached([args.model] if args.model else MODEL_PREFERENCE)
        if found:
            model_id = load_model(*found)
    if url and model_id:
        if check_api(url, model_id):
            reply = test_request(url, model_id, args.prompt)
    if not dashboard(url, model_id, reply):
        return 1

    os.environ["FOUNDRY_HOST"] = url
    os.environ["FOUNDRY_MODEL"] = model_id
    log.info("Environment set: FOUNDRY_HOST=%s FOUNDRY_MODEL=%s", url, model_id)
    if args.print_env:
        print(f"export FOUNDRY_HOST={shlex.quote(url)}\nexport FOUNDRY_MODEL={shlex.quote(model_id)}")
    if args.run:
        cmd = [sys.executable, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"), *args.main_args]
        log.info("Starting: %s", " ".join(cmd))
        return subprocess.call(cmd, env=os.environ.copy())
    return 0


if __name__ == "__main__":
    sys.exit(main())
