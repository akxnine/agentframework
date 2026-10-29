#!/usr/bin/env python3
"""System + Foundry Local model preflight with a live terminal dashboard.

Usage:
  python system_check.py                 # one-shot dashboard, then one dashboard per cached model
                                         # (stops/restarts the Foundry server; it is stopped at the end)
  python system_check.py --no-models     # skip the per-model dashboards (server left untouched)
  python system_check.py -v              # also trace raw output of every command run
  python system_check.py --watch         # refresh every 2s until Ctrl+C
  python system_check.py --model NAME    # override FOUNDRY_MODEL
Exit code is 1 if any check is critical (e.g. server down, model missing, very low free RAM).
"""
import argparse
import json
import logging
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
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

GB = 1024 ** 3
console = Console()
logging.basicConfig(level=logging.INFO, format="%(message)s", datefmt="[%H:%M:%S]",
                    handlers=[RichHandler(console=console, show_path=False, markup=False)])
log = logging.getLogger("system_check")


def sh(*cmd: str, timeout: int = 20) -> str:
    """Run a command and return stdout+stderr; every call is traced (command, exit code, duration)."""
    log.info("$ %s", " ".join(cmd))
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("  ! %s failed after %.1fs: %s", cmd[0], time.time() - t0, exc)
        return ""
    out = (p.stdout + p.stderr).strip()
    (log.info if p.returncode == 0 else log.warning)("  -> exit %s in %.1fs (%d chars output)",
                                                     p.returncode, time.time() - t0, len(out))
    for line in out.splitlines():
        log.debug("  | %s", line)
    return out


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
    model = model.lower()
    return [m for m in served if model and (m.lower() == model or m.lower().startswith(model + "-"))]


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


# metric -> (minimum, recommended, unit); for phi-4-mini / gemma-4-e2b-it class int4 models
REQUIREMENTS = {
    "CPU physical cores": (4, 8, ""),
    "RAM total": (8, 16, "GB"),
    "RAM available": (3, 6, "GB"),
    "Disk free": (10, 20, "GB"),
    "GPU memory": (4, 8, "GB"),
}


def requirements_table(vm, disk, gpu_list: list[dict]) -> Table:
    """Current vs minimum/recommended values; GPU memory is total VRAM (unified memory on Apple Silicon)."""
    gpu_total = max((g["total"] for g in gpu_list), default=0.0)
    current = {
        "CPU physical cores": float(psutil.cpu_count(logical=False) or psutil.cpu_count() or 0),
        "RAM total": vm.total / GB,
        "RAM available": vm.available / GB,
        "Disk free": disk.free / GB,
        "GPU memory": gpu_total,
    }
    t = Table(expand=True)
    for col, just in (("Metric", "left"), ("Current", "right"), ("Minimum", "right"), ("Recommended", "right"),
                      ("Status", "center")):
        t.add_column(col, justify=just)
    for name, (lo, rec, unit) in REQUIREMENTS.items():
        cur = current[name]
        if cur < lo:
            status = "[red]below minimum[/]"
        elif cur < rec:
            status = "[yellow]meets minimum[/]"
        else:
            status = "[green]recommended[/]"
        u = f" {unit}" if unit else ""
        t.add_row(name, f"{cur:.1f}{u}", f"{lo}{u}", f"{rec}{u}", status)
    return t


def stop_server() -> None:
    log.info("Stopping Foundry server")
    sh("foundry", "server", "stop", timeout=60)


def start_server() -> str:
    """Start the daemon (output not captured: it can hold pipes open) and return its base URL, or '' on failure."""
    log.info("Starting Foundry server")
    log.info("$ foundry server start")
    subprocess.run(["foundry", "server", "start"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=120)
    for attempt in range(1, 31):
        try:
            out = sh("foundry", "server", "status", "--output", "json")
            d = json.loads(out[out.index("{"):])
            log.info("  server state=%s (poll %d/30)", d.get("state"), attempt)
            if str(d.get("state", "")).lower() == "ready" and d.get("webUrls"):
                host = d["webUrls"][0].rstrip("/").split("/openai")[0]
                log.info("Server ready at %s", host)
                return host
        except ValueError:
            log.warning("  could not parse server status")
        time.sleep(1)
    log.error("Server did not become ready within 30s")
    return ""


def cached_models() -> list[str]:
    out = sh("foundry", "model", "list", "--cached", "--output", "json", timeout=120)
    try:
        models = json.JSONDecoder().raw_decode(out[out.index('{"models"'):])[0]["models"]
    except (ValueError, KeyError):
        log.warning("Could not parse cached model list")
        return []
    aliases = list(dict.fromkeys(m["alias"] for m in models if m.get("alias")))
    log.info("Cached models: %s", ", ".join(aliases) or "none")
    return aliases


def per_model_dashboards() -> None:
    """Restart the server, then load each cached model on its own and print a dashboard for it.
    The server is always stopped at the end."""
    if not shutil.which("foundry"):
        console.print("[yellow]foundry CLI not found; skipping per-model dashboards.[/]")
        return
    try:
        console.rule("[bold cyan]Per-model dashboards[/]", style="cyan")
        stop_server()
        if not start_server():
            console.print("[red]Server failed to start; skipping per-model dashboards.[/]")
            return
        models = cached_models()
        if not models:
            console.print("[yellow]No cached models; skipping per-model dashboards.[/]")
        for i, alias in enumerate(models, 1):
            console.rule(f"[bold magenta]Model {i}/{len(models)}: {alias}[/]", style="magenta")
            stop_server()
            host = start_server()
            if not host:
                console.print(f"[red]Server failed to start for {alias}.[/]")
                continue
            log.info("Loading model %s (can take a while)", alias)
            t0 = time.time()
            load = sh("foundry", "model", "load", alias, timeout=900)
            log.info("Model %s load finished in %.1fs", alias, time.time() - t0)
            log_line = load.splitlines()[-1] if load else ""
            if log_line:
                console.print(Text(log_line, style="dim"))
            log.info("Collecting system details for %s", alias)
            body, _ = build(alias, host)
            console.print(body)
    finally:
        stop_server()
        console.print("[bold green]Foundry server stopped.[/]")


def build(model: str, host: str) -> tuple[Group, bool]:
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    disk = shutil.disk_usage(os.path.expanduser("~"))
    gpu_list = gpus()
    st = foundry_state(host, model)
    ram_free = vm.available / GB

    def lbl(s: str) -> Text:
        return Text(s, style="bold cyan")

    sysinfo = Table.grid(padding=(0, 2))
    sysinfo.add_row(lbl("OS"), f"{platform.system()} {platform.release()} ({platform.machine()})")
    sysinfo.add_row(lbl("CPU"), f"{platform.processor() or platform.machine()} · {psutil.cpu_count(logical=False)}P/"
                                f"{psutil.cpu_count()}L cores")
    sysinfo.add_row(lbl("CPU load"), bar(psutil.cpu_percent(interval=0.3) / 100))
    ram_style = "red" if ram_free < 3 else "yellow" if ram_free < 6 else "green"
    sysinfo.add_row(lbl("RAM"), Text.assemble(bar(vm.percent / 100), f"  {vm.used / GB:.1f}/{vm.total / GB:.1f} GB, ",
                                              (f"{ram_free:.1f} GB available", ram_style)))
    sw_style = "green" if sw.used / GB <= 1 else "red"
    sysinfo.add_row(lbl("Swap"), Text.assemble(bar(sw.percent / 100 if sw.total else 0),
                                               (f"  {sw.used / GB:.1f}/{sw.total / GB:.1f} GB", sw_style)))
    disk_style = "red" if disk.free / GB < 10 else "yellow" if disk.free / GB < 20 else "green"
    sysinfo.add_row(lbl("Disk (home)"), Text.assemble(bar(disk.used / disk.total),
                                                      (f"  {disk.free / GB:.0f} GB free", disk_style)))
    for g in gpu_list:
        label = Text.assemble(bar(g["used"] / g["total"] if g["total"] else 0),
                              f"  {g['used']:.1f}/{g['total']:.1f} GB · {g['kind']}")
        sysinfo.add_row(lbl(f"GPU: {g['name']}"), label)
    if not gpu_list:
        sysinfo.add_row(lbl("GPU"), Text("none detected", style="yellow"))

    matched = matches(model, st["served"])
    cfg = Table.grid(padding=(0, 2))
    cfg.add_row(lbl("Host"), st["host"] or "[red]unset[/]")
    cfg.add_row(lbl("Server"), "[bold green]up[/]" if st["up"] else "[bold red]down[/]")
    cfg.add_row(lbl("Model (FOUNDRY_MODEL)"), f"[bold magenta]{model}[/]" if model else "[yellow]unset[/]")
    cfg.add_row(lbl("Served entries"), Text.assemble(
        f"{len(st['served'])} total; matching: ",
        (", ".join(matched), "green") if matched else ("-", "red" if model else "dim")))
    cfg.add_row(lbl("Loaded in memory"),
                "[bold green]yes[/]" if st["loaded"] else "[yellow]no (loads on first request)[/]")
    for var in ("FOUNDRY_HOST", "OUTPUT_FOLDER", "DOCS_FOLDER"):
        val = os.getenv(var)
        cfg.add_row(lbl(var), val if val else Text("-", style="dim"))

    def cache_line(ln: str) -> Text:
        if ln.rstrip("| ").endswith("●"):
            return Text(ln, style="bold green")
        return Text(ln, style="dim" if set(ln) <= set("+-| ") else "white")

    cache = Panel(Group(*[cache_line(x) for x in st["cache"].splitlines()])
                  if st["cli"] else Text("(foundry CLI unavailable)", style="yellow"),
                  title="[bold]Cache models[/]", border_style="blue")

    tips = Table.grid(padding=(0, 1))
    icons = {"ok": "[green]✔[/]", "warn": "[yellow]▲[/]", "crit": "[red]✖[/]"}
    adv = advice(model, ram_free, sw.used / GB, gpu_list, st, disk.free / GB)
    for sev, msg, fix in adv:
        tips.add_row(icons[sev], Text(msg, style={"ok": "green", "warn": "yellow", "crit": "bold red"}[sev]))
        if fix:
            tips.add_row("", Text("→ Fix: " + fix, style="cyan"))

    body = Group(Panel(sysinfo, title="System resources", border_style="cyan"),
                 Panel(requirements_table(vm, disk, gpu_list), title="Minimum / recommended values",
                       border_style="green"),
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
    ap.add_argument("--no-models", action="store_true", help="skip the per-cached-model dashboards")
    ap.add_argument("-v", "--verbose", action="store_true", help="also trace raw command output")
    args = ap.parse_args()
    # the full-screen --watch display would be garbled by trace lines
    log.setLevel(logging.DEBUG if args.verbose else logging.WARNING if args.watch else logging.INFO)

    log.info("System check starting (model=%s, host=%s)", args.model or "unset", args.host or "auto")
    body, crit = build(args.model, args.host)
    if not args.watch:
        console.print(body)
        if not args.no_models:
            per_model_dashboards()
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
