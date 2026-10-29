"""Foundry Local server status and start/stop/restart helpers used by the dashboard."""
import json
import os
import shlex
import subprocess
import sys
import time
from urllib.parse import urlparse

import httpx

PREFLIGHT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "precheck",
                         "foundry_preflight.py")


def _cli_json(*args: str, timeout: int = 30) -> dict:
    try:
        proc = subprocess.run(["foundry", *args, "--output", "json"], capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
        out = proc.stdout + proc.stderr
        return json.loads(out[out.index("{"):])
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def _cli_version() -> str:
    try:
        return subprocess.run(["foundry", "--version"], capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "not installed"


def status() -> dict:
    """Snapshot of the server: CLI state, configured host/model, API reachability and model lists."""
    st = _cli_json("server", "status")
    host = os.getenv("FOUNDRY_HOST", "").rstrip("/")
    model = os.getenv("FOUNDRY_MODEL", "")
    urls = st.get("webUrls") or []
    api_host = host or (urls[0] if urls else "")
    api_ok, latency, served = False, None, []
    if api_host:
        try:
            t0 = time.time()
            r = httpx.get(f"{api_host}/v1/models", timeout=3)
            r.raise_for_status()
            latency = (time.time() - t0) * 1000
            served = [m.get("id") for m in r.json().get("data", [])]
            api_ok = True
        except httpx.HTTPError:
            pass
    running = bool(st.get("running"))
    return {
        "cli_version": _cli_version(),
        "running": running,
        "state": st.get("state", "unknown" if not st else ""),
        "urls": urls,
        "env_host": host,
        "env_model": model,
        "port_match": (not host or not urls) or urlparse(host).port in {urlparse(u).port for u in urls},
        "api_ok": api_ok,
        "latency_ms": latency,
        "served": served,
        "model_served": bool(model) and model in served,
        "loaded": _cli_json("model", "list", "--loaded").get("models", []) if running else [],
        "cached": _cli_json("model", "list", "--cached").get("models", []),
        "checked_at": time.time(),
    }


def start() -> tuple[bool, str]:
    """Start the server and load a model through the preflight; exports FOUNDRY_HOST/FOUNDRY_MODEL to this process."""
    proc = subprocess.run([sys.executable, PREFLIGHT, "--print-env"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          stdin=subprocess.DEVNULL, text=True, timeout=1200)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-5:]
        return False, "Preflight failed: " + " | ".join(tail)
    for line in proc.stdout.splitlines():
        if line.startswith("export "):
            k, _, v = line[7:].partition("=")
            os.environ[k] = shlex.split(v)[0] if v else ""
    return True, f"Server ready at {os.getenv('FOUNDRY_HOST')} with model {os.getenv('FOUNDRY_MODEL')}"


def stop() -> tuple[bool, str]:
    try:
        subprocess.run(["foundry", "server", "stop"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"Could not stop server: {e}"
    return True, "Server stopped"


def restart() -> tuple[bool, str]:
    ok, msg = stop()
    if not ok:
        return ok, msg
    time.sleep(2)
    return start()
