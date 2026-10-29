#!/usr/bin/env python3
"""System + Foundry Local model preflight with a live terminal dashboard.

Usage:
  python system_check.py                 # one-shot dashboard
  python system_check.py --watch         # refresh every 2s until Ctrl+C
  python system_check.py --model NAME    # override FOUNDRY_MODEL
Exit code is 1 if any check is critical (e.g. server down, model missing, very low free RAM).
"""
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

import httpx
import psutil
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

GB = 1024 ** 3
console = Console()


def sh(*cmd: str, timeout: int = 20) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def bar(frac: float, width: int = 20) -> Text:
    frac = max(0.0, min(1.0, frac))
    color = "green" if frac < 0.7 else "yellow" if frac < 0.85 else "red"
    n = round(frac * width)
    t = Text("█" * n, style=color)
    t.append("░" * (width - n), style="dim")
    t.append(f" {frac * 100:3.0f}%")
    return t


def gpus() -> list[dict]:
    """GPU name + VRAM (total/used GB). Apple Silicon uses unified memory, so VRAM == system RAM."""
    out: list[dict] = []
    if shutil.which("nvidia-smi"):
        q = sh("nvidia-smi", "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
               "--format=csv,noheader,nounits")
        for line in q.splitlines():
            try:
                name, total, used, util, temp = [x.strip() for x in line.split(",")]
                out.append({"name": name, "total": float(total) / 1024, "used": float(used) / 1024,
                            "util": float(util), "temp": temp, "kind": "NVIDIA"})
            except ValueError:
                continue
    elif sys.platform == "darwin":
        info = sh("system_profiler", "SPDisplaysDataType", "-json", timeout=30)
        try:
            for d in json.loads(info).get("SPDisplaysDataType", []):
                vm = psutil.virtual_memory()
                out.append({"name": d.get("sppci_model", "Apple GPU"), "total": vm.total / GB,
                            "used": (vm.total - vm.available) / GB, "util": None, "temp": "-",
                            "kind": "Unified memory (shared with RAM)"})
        except ValueError:
            pass
    return out


def foundry_state(host: str, model: str) -> dict:
    """Server reachability, served models, and CLI-reported loaded/cached models."""
    s: dict = {"cli": shutil.which("foundry"), "host": host, "up": False, "served": [], "loaded": "", "cache": "",
               "info": ""}
    if s["cli"]:
        s["cache"] = sh("foundry", "cache", "ls")
        s["loaded"] = "\n".join(ln for ln in s["cache"].splitlines() if ln.rstrip("| ").endswith("●"))
        if not host:
            try:
                out = sh("foundry", "server", "status", "--output", "json")
                urls = json.loads(out[out.index("{"):]).get("webUrls") or []
                host = s["host"] = urls[0].rstrip("/").split("/openai")[0] if urls else ""
            except (ValueError, IndexError):
                pass
    if host:
        try:
            r = httpx.get(f"{host}/v1/models", timeout=5)
            r.raise_for_status()
            s["up"] = True
            s["served"] = [m["id"] for m in r.json().get("data", [])]
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    return s


def matches(model: str, served: list[str]) -> list[str]:
    """Foundry serves variant IDs (e.g. gemma-4-e2b-it-generic-gpu:3); the alias is their prefix."""
    return [m for m in served if model and (m == model or m.startswith(model + "-"))]


def model_size_gb(model: str, cache: str) -> float | None:
    """Size column of the model's row in `foundry cache ls`."""
    for ln in cache.splitlines():
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) >= 3 and cells[0] == model:
            m = re.match(r"([\d.]+)\s*(GB|MB)", cells[1])
            if m:
                return float(m.group(1)) / (1 if m.group(2) == "GB" else 1024)
    return None


def advice(model: str, ram_free: float, swap_used: float, gpu_list: list[dict], st: dict,
           disk_free: float = 99) -> list[tuple[str, str, str]]:
    """(severity, finding, suggested fix) triples; severity is ok/warn/crit. Fix may be empty."""
    a: list[tuple[str, str, str]] = []
    if not st["cli"]:
        a.append(("warn", "foundry CLI not found on PATH; model/cache details unavailable.",
                  "Install Foundry Local and reopen the terminal: `brew install microsoft/foundrylocal/foundrylocal` "
                  "(macOS) or `winget install Microsoft.FoundryLocal` (Windows)."))
    if not st["up"]:
        a.append(("crit", "Foundry Local server not reachable.",
                  "`foundry server start` (or `foundry server restart`), then `python foundry_preflight.py --run`. "
                  "Check `foundry server logs` if it keeps failing."))
    elif not model:
        a.append(("warn", "FOUNDRY_MODEL not set.",
                  "Pass `--model <alias>` or run `python foundry_preflight.py --run` to export it."))
    elif not matches(model, st["served"]):
        a.append(("crit", f"Model '{model}' is not served ({len(st['served'])} catalog entries served).",
                  f"`foundry model ls` to find the alias, then `foundry model download {model}` and "
                  f"`foundry model load {model}`."))
    else:
        a.append(("ok", f"Model '{model}' is served.", ""))
    size = model_size_gb(model, st["cache"])
    if size and size > ram_free:
        a.append(("warn", f"Model ({size:.1f} GB) is larger than available memory ({ram_free:.1f} GB).",
                  "Use a smaller int4 model (e.g. qwen2.5-3b/7b, Phi-4-mini) or free memory first."))
    if ram_free < 1.5:
        a.append(("crit", f"Only {ram_free:.1f} GB RAM available.",
                  "Quit memory-heavy apps (browsers, Docker, IDEs), `foundry model unload <alias>` for unused models."))
    elif ram_free < 3:
        a.append(("warn", f"Low available RAM ({ram_free:.1f} GB).",
                  "Prefer a smaller int4 model, keep requests <= 4K tokens, close other apps."))
    if swap_used > 1:
        a.append(("warn", f"{swap_used:.1f} GB swap in use; inference will be slow.",
                  "Free memory (close apps), restart the daemon with `foundry server restart`, use a smaller model."))
    for g in gpus_discrete(gpu_list):
        free = g["total"] - g["used"]
        if free < 1.5:
            a.append(("warn", f"{g['name']}: only {free:.1f} GB VRAM free.",
                      "Lower per-request context, pick a smaller model, or close GPU apps (see `nvidia-smi`)."))
    if not gpu_list:
        a.append(("warn", "No GPU detected; Foundry Local will use the CPU variant (slower).",
                  "Update GPU drivers, or use a smaller model for CPU inference."))
    if disk_free < 10:
        a.append(("warn", f"Only {disk_free:.0f} GB free disk.",
                  "Model downloads need space: `foundry cache ls`, then remove unused ones with `foundry cache rm <alias>`."))
    if st["loaded"] and model and model not in st["loaded"]:
        a.append(("warn", "A different model appears to be loaded.",
                  "`foundry model unload <other-alias>` to free memory, then `foundry model load <alias>`."))
    a.append(("ok", "General tips: chunk large PDFs, set max_tokens, keep temperature=0.", "See docs/optimization.md."))
    return a


def gpus_discrete(gpu_list: list[dict]) -> list[dict]:
    return [g for g in gpu_list if g["kind"] == "NVIDIA"]


def build(model: str, host: str) -> tuple[Group, bool]:
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    disk = shutil.disk_usage(os.path.expanduser("~"))
    gpu_list = gpus()
    st = foundry_state(host, model)
    ram_free = vm.available / GB

    sysinfo = Table.grid(padding=(0, 2))
    sysinfo.add_row("OS", f"{platform.system()} {platform.release()} ({platform.machine()})")
    sysinfo.add_row("CPU", f"{platform.processor() or platform.machine()} · {psutil.cpu_count(logical=False)}P/"
                           f"{psutil.cpu_count()}L cores")
    sysinfo.add_row("CPU load", bar(psutil.cpu_percent(interval=0.3) / 100))
    sysinfo.add_row("RAM", Text.assemble(bar(vm.percent / 100), f"  {vm.used / GB:.1f}/{vm.total / GB:.1f} GB, "
                                                                 f"{ram_free:.1f} GB available"))
    sysinfo.add_row("Swap", Text.assemble(bar(sw.percent / 100 if sw.total else 0),
                                          f"  {sw.used / GB:.1f}/{sw.total / GB:.1f} GB"))
    sysinfo.add_row("Disk (home)", Text.assemble(bar(disk.used / disk.total), f"  {disk.free / GB:.0f} GB free"))
    for g in gpu_list:
        label = Text.assemble(bar(g["used"] / g["total"] if g["total"] else 0),
                              f"  {g['used']:.1f}/{g['total']:.1f} GB · {g['kind']}")
        sysinfo.add_row(f"GPU: {g['name']}", label)
    if not gpu_list:
        sysinfo.add_row("GPU", Text("none detected", style="yellow"))

    cfg = Table.grid(padding=(0, 2))
    cfg.add_row("Host", st["host"] or "[red]unset[/]")
    cfg.add_row("Server", "[green]up[/]" if st["up"] else "[red]down[/]")
    cfg.add_row("Model (FOUNDRY_MODEL)", model or "[yellow]unset[/]")
    cfg.add_row("Served entries", f"{len(st['served'])} total; matching: {', '.join(matches(model, st['served'])) or '-'}")
    cfg.add_row("Loaded in memory", "yes" if st["loaded"] else "no (loads on first request)")
    for var in ("FOUNDRY_HOST", "OUTPUT_FOLDER", "DOCS_FOLDER"):
        cfg.add_row(var, os.getenv(var, "-"))
    cache = Panel(Group(*[Text(x, style="dim") for x in (st["cache"],) if x])
                  if st["cli"] else "(foundry CLI unavailable)", title="Cache models",
                  border_style="dim")

    tips = Table.grid(padding=(0, 1))
    icons = {"ok": "[green]✔[/]", "warn": "[yellow]▲[/]", "crit": "[red]✖[/]"}
    adv = advice(model, ram_free, sw.used / GB, gpu_list, st, disk.free / GB)
    for sev, msg, fix in adv:
        tips.add_row(icons[sev], msg)
        if fix:
            tips.add_row("", Text("→ Fix: " + fix, style="cyan"))

    body = Group(Panel(sysinfo, title="System resources", border_style="cyan"),
                 Panel(cfg, title="Foundry Local / model config", border_style="magenta"), cache,
                 Panel(tips, title="Findings", border_style="yellow"),
                 Text(time.strftime("updated %H:%M:%S"), style="dim", justify="right"))
    return body, any(s == "crit" for s, _, _ in adv)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=os.getenv("FOUNDRY_MODEL", ""))
    ap.add_argument("--host", default=os.getenv("FOUNDRY_HOST", "").rstrip("/"))
    ap.add_argument("--watch", action="store_true", help="refresh continuously")
    ap.add_argument("--interval", type=float, default=2.0)
    args = ap.parse_args()

    body, crit = build(args.model, args.host)
    if not args.watch:
        console.print(body)
        return 1 if crit else 0
    try:
        with Live(body, console=console, refresh_per_second=4, screen=True) as live:
            while True:
                time.sleep(args.interval)
                body, crit = build(args.model, args.host)
                live.update(body)
    except KeyboardInterrupt:
        return 1 if crit else 0


if __name__ == "__main__":
    sys.exit(main())
