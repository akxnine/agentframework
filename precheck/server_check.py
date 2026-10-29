"""Lightweight Foundry server check for main.py, shown as a small dashboard.

precheck/foundry_preflight.py stays the standalone, detailed test; this class only verifies the server is usable and
falls back to the preflight (to start/load the model) when it is not.
"""
import os
import shlex
import subprocess
import sys
import time

from rich.panel import Panel
from rich.table import Table

from recon.config import Settings
from recon.console import console

PREFLIGHT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "foundry_preflight.py")


class ServerCheck:
    def __init__(self, settings: Settings):
        self.s = settings
        self.status = "PENDING"
        self.note = ""
        self.secs = 0.0

    def ensure(self) -> None:
        """Verify the server; on failure let the preflight start/load it, then re-verify. Exits on failure."""
        t0 = time.time()
        try:
            try:
                self.s.check_server()
                self.status = "READY"
            except RuntimeError as e:
                self.note = str(e).splitlines()[0][:100]
                self._run_preflight()
                self.s.check_server()
                self.status = "READY (via preflight)"
        except (RuntimeError, SystemExit) as e:
            self.status, self.note = "FAILED", str(e).splitlines()[0][:100] if str(e) else self.note
            self.secs = time.time() - t0
            self.show()
            raise SystemExit(1)
        self.secs = time.time() - t0
        self.show()

    def _run_preflight(self) -> None:
        with console.status("[yellow]Server not ready, running precheck/foundry_preflight.py ...[/]"):
            proc = subprocess.run([sys.executable, PREFLIGHT, "--print-env"], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True)
        if proc.returncode != 0:
            raise SystemExit("Preflight failed; run `python precheck/foundry_preflight.py` for details.")
        env = {}
        for line in proc.stdout.splitlines():
            if line.startswith("export "):
                k, _, v = line[7:].partition("=")
                env[k] = shlex.split(v)[0] if v else ""
        self.s.foundry_host = env.get("FOUNDRY_HOST", "").rstrip("/")
        self.s.model = env.get("FOUNDRY_MODEL", "")

    def show(self) -> None:
        ok = self.status.startswith("READY")
        colour = "green" if ok else "red"
        t = Table.grid(padding=(0, 2))
        t.add_column(style="bold cyan")
        t.add_column()
        t.add_row("Server", self.s.foundry_host or "-")
        t.add_row("Model", self.s.model or "-")
        t.add_row("Status", f"[bold {colour}]{'✔' if ok else '✘'} {self.status}[/]")
        t.add_row("Checked in", f"{self.secs:.1f}s")
        if self.note:
            t.add_row("Note", f"[dim]{self.note}[/]")
        console.print(Panel(t, title="Foundry server", border_style=colour, expand=False))

    @staticmethod
    def stop() -> None:
        with console.status("[cyan]Stopping Foundry server ...[/]"):
            try:
                subprocess.run(["foundry", "server", "stop"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=60)
                console.print("[green]✔[/] Foundry server stopped")
            except (OSError, subprocess.SubprocessError) as e:
                console.print(f"[yellow]Could not stop Foundry server: {e}[/]")
