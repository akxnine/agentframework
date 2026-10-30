import os
from dataclasses import dataclass, field

from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .excel_io import norm_acct
from .models import Discrepancy, Statement, Trade


@dataclass
class LLMCall:
    request: str
    purpose: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    ok: bool = True

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


# Filled by extraction.py, cleared at the start of each pipeline run.
llm_calls: list[LLMCall] = []


@dataclass
class DocRow:
    file: str
    statements: int = 0
    trades: int = 0
    error: str = ""


@dataclass
class Summary:
    docs: list[DocRow] = field(default_factory=list)
    statements: list[Statement] = field(default_factory=list)
    client_trades: list[Trade] = field(default_factory=list)
    discrepancies: list[Discrepancy] = field(default_factory=list)


def _bar(good: int, bad: int, width: int = 20) -> Text:
    total = good + bad
    filled = round(width * good / total) if total else 0
    t = Text()
    t.append("█" * filled, style="green")
    t.append("█" * (width - filled), style="red" if total else "grey37")
    return t


def requests_panel() -> Panel:
    t = Table(expand=True, title="Every model request")
    t.add_column("#", justify="right")
    t.add_column("Request")
    t.add_column("Purpose")
    t.add_column("Prompt", justify="right")
    t.add_column("Completion", justify="right")
    t.add_column("Total", justify="right")
    t.add_column("Time", justify="right")
    t.add_column("tok/s", justify="right")
    t.add_column("Result", justify="center")
    for i, c in enumerate(llm_calls, 1):
        speed = f"{c.completion_tokens / c.seconds:.1f}" if c.seconds else "-"
        t.add_row(str(i), c.request, c.purpose, f"{c.prompt_tokens:,}", f"{c.completion_tokens:,}", f"{c.total:,}",
                  f"{c.seconds:.1f}s", speed, "[green]OK[/]" if c.ok else "[red]RETRY[/]")
    prompt = sum(c.prompt_tokens for c in llm_calls)
    comp = sum(c.completion_tokens for c in llm_calls)
    secs = sum(c.seconds for c in llm_calls)
    if llm_calls:
        t.add_section()
        t.add_row("", "[bold]Total[/]", f"{len(llm_calls)} requests", f"[bold]{prompt:,}[/]", f"[bold]{comp:,}[/]",
                  f"[bold]{prompt + comp:,}[/]", f"{secs:.1f}s", f"{comp / secs:.1f}" if secs else "-", "")
    retries = sum(1 for c in llm_calls if not c.ok)
    foot = Text(f"Input/output split: {prompt / (prompt + comp) * 100:.0f}% / {comp / (prompt + comp) * 100:.0f}%"
                f"   retries: {retries}" if prompt + comp else "No model requests were made.", style="dim")
    return Panel(Group(t, foot), title="[bold cyan]LLM ACTIVITY[/]", border_style="cyan")


def reconciliation_panel(s: Summary) -> Panel:
    advisor = [t for st in s.statements for t in st.trades]
    flagged = [d for d in s.discrepancies if d.kind != "MISSING_IN_ADVISOR"]
    suspicious = [d for d in s.discrepancies if d.kind == "PRICE"]
    good = max(len(advisor) - len(flagged), 0)

    tiles = [
        Panel(f"[bold]{len(s.docs)}[/]\n{sum(1 for d in s.docs if not d.error)} ok / "
              f"{sum(1 for d in s.docs if d.error)} failed", title="Documents", width=22),
        Panel(f"[bold]{len(s.statements)}[/]\naccounts", title="Statements", width=22),
        Panel(f"[bold]{len(advisor)}[/] advisor\n[bold]{len(s.client_trades)}[/] client", title="Trades", width=22),
        Panel(f"[bold green]{good}[/] clean\n[bold red]{len(flagged)}[/] flagged", title="Match", width=22),
        Panel(f"[bold {'red' if suspicious else 'green'}]{len(suspicious)}[/]\nprice anomalies",
              title="Suspicious", width=22),
    ]

    docs = Table(expand=True, title="Documents")
    for col in ("File", "Statements", "Trades", "Status"):
        docs.add_column(col, justify="left" if col in ("File", "Status") else "right")
    for d in s.docs:
        docs.add_row(d.file, str(d.statements), str(d.trades), f"[red]{d.error}[/]" if d.error else "[green]extracted[/]")

    acct = Table(expand=True, title="Accounts")
    for col in ("Broker", "Account", "Holder", "Trades", "Health", "Flags", "Verdict"):
        acct.add_column(col, justify="right" if col in ("Trades", "Flags") else "left")
    for st in s.statements:
        key = norm_acct(st.account_number)
        mine = [d for d in s.discrepancies if norm_acct(d.account) == key]
        sus = sum(1 for d in mine if d.kind == "PRICE")
        bad = sum(1 for d in mine if d.kind != "MISSING_IN_ADVISOR")
        verdict = "[red]SUSPICIOUS[/]" if sus else "[yellow]REVIEW[/]" if mine else "[green]CLEAN[/]"
        acct.add_row(st.broker, st.account_number, st.account_holder or "-", str(len(st.trades)),
                     _bar(max(len(st.trades) - bad, 0), bad), str(len(mine)), verdict)

    kinds = Table(expand=True, title="Discrepancy types")
    kinds.add_column("Type")
    kinds.add_column("Count", justify="right")
    kinds.add_column("")
    counts: dict[str, int] = {}
    for d in s.discrepancies:
        counts[d.kind] = counts.get(d.kind, 0) + 1
    top = max(counts.values(), default=1)
    for k, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        kinds.add_row("[red]" + k + "[/]" if k == "PRICE" else k, str(n), "▇" * max(1, round(12 * n / top)))
    if not counts:
        kinds.add_row("None", "0", "")

    sym = Table(expand=True, title="Most flagged symbols")
    sym.add_column("Symbol")
    sym.add_column("Flags", justify="right")
    by_sym: dict[str, int] = {}
    for d in s.discrepancies:
        by_sym[d.symbol] = by_sym.get(d.symbol, 0) + 1
    for k, n in sorted(by_sym.items(), key=lambda kv: -kv[1])[:5]:
        sym.add_row(k, str(n))

    verdict = ("[bold red]ATTENTION: suspicious pricing detected[/]" if suspicious
               else "[bold green]All clear: no suspicious activity[/]")
    return Panel(Group(Columns(tiles, expand=True), docs, acct, Columns([kinds, sym], expand=True), Text.from_markup(verdict)),
                 title="[bold magenta]RECONCILIATION[/]", border_style="magenta")


def print_dashboards(s: Summary, out_dir: str) -> str:
    """Print both dashboards and save them as an HTML page; returns the HTML path."""
    console = Console(record=True, width=130)
    console.print(requests_panel())
    console.print(reconciliation_panel(s))
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "dashboard.html")
    console.save_html(path)
    return path
