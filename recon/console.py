"""Colourful, interactive console output (rich) shared by main.py and the pipeline."""
import logging
from dataclasses import dataclass, field

from rich.console import Console
from rich.logging import RichHandler
from rich.rule import Rule
from rich.table import Table

console = Console(stderr=True, width=max(Console().width, 140))

STEP_STYLES = {0: "cyan", 1: "blue", 2: "magenta", 3: "yellow", 4: "green", 5: "red"}


def setup_logging() -> None:
    handler = RichHandler(console=console, show_path=False, markup=True, rich_tracebacks=True,
                          log_time_format="[%H:%M:%S]")
    logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def step(n: int, title: str) -> None:
    console.print()
    console.print(Rule(f"[bold]Step {n}[/bold] · {title}", style=STEP_STYLES.get(n, "white"), align="left"))


@dataclass
class ExtractStats:
    """Per-PDF details collected during extraction for the log table."""
    file: str
    pages: int = 0
    chars: int = 0
    attempts: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    read_secs: float = 0.0
    llm_secs: float = 0.0
    statements: int = 0
    trades: int = 0
    status: str = "PENDING"
    error: str = ""
    total_secs: float = 0.0
    accounts: list[str] = field(default_factory=list)


def _status(s: ExtractStats) -> str:
    if s.status == "OK":
        return "[bold green]✔ OK[/]" if s.attempts <= 1 else "[bold yellow]✔ OK (retry)[/]"
    return "[bold red]✘ FAILED[/]"


def extraction_table(rows: list[ExtractStats]) -> Table:
    t = Table(title="PDF extraction log", title_style="bold cyan", header_style="bold white on blue",
              show_lines=False, expand=False)
    for col, kw in [("#", {"justify": "right"}), ("File", {"style": "bold", "overflow": "fold"}), ("Status", {"no_wrap": True}),
                    ("Pages", {"justify": "right"}), ("Chars", {"justify": "right"}),
                    ("Tries", {"justify": "right"}), ("Tok in/out", {"justify": "right", "no_wrap": True}),
                    ("Read", {"justify": "right"}), ("LLM", {"justify": "right"}),
                    ("Total", {"justify": "right", "style": "bold"}), ("Accts", {"justify": "right"}),
                    ("Trades", {"justify": "right"}), ("Notes", {"overflow": "fold", "max_width": 30})]:
        t.add_column(col, **kw)
    for i, s in enumerate(rows, 1):
        slow = "yellow" if s.total_secs > 30 else "green"
        t.add_row(str(i), s.file, _status(s), str(s.pages), f"{s.chars:,}", str(s.attempts),
                  f"{s.prompt_tokens:,}/{s.completion_tokens:,}", f"{s.read_secs:.2f}s",
                  f"{s.llm_secs:.1f}s", f"[{slow}]{s.total_secs:.1f}s[/]", str(s.statements), str(s.trades),
                  f"[red]{s.error}[/]" if s.error else ", ".join(s.accounts))
    ok = [s for s in rows if s.status == "OK"]
    t.add_section()
    t.add_row("", f"[bold]{len(ok)}/{len(rows)} OK[/]", "", str(sum(s.pages for s in rows)),
              f"{sum(s.chars for s in rows):,}", str(sum(s.attempts for s in rows)),
              f"{sum(s.prompt_tokens for s in rows):,}/{sum(s.completion_tokens for s in rows):,}",
              f"{sum(s.read_secs for s in rows):.2f}s", f"{sum(s.llm_secs for s in rows):.1f}s",
              f"[bold]{sum(s.total_secs for s in rows):.1f}s[/]", str(sum(s.statements for s in rows)),
              str(sum(s.trades for s in rows)), "")
    return t
