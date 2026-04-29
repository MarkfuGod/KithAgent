"""Small terminal UI helpers for the backend-first CLI.

Rich is used when available and stdout is interactive. Plain stdout remains the
fallback so daemonized/scripted invocations stay predictable.
"""

from __future__ import annotations

from contextlib import contextmanager
import sys
from typing import Iterable, Sequence

try:  # pragma: no cover - exercised through behavior, not import plumbing.
    from rich import box
    from rich.columns import Columns
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.table import Table

    _RICH_AVAILABLE = True
except Exception:  # pragma: no cover
    box = None
    Columns = None
    Console = None
    Panel = None
    Confirm = None
    Prompt = None
    Table = None
    _RICH_AVAILABLE = False


class CLIUI:
    """Tiny wrapper that keeps Rich usage optional and easy to test."""

    def __init__(
        self,
        *,
        json_output: bool = False,
        force_plain: bool = False,
        stdout=None,
        stdin=None,
    ) -> None:
        self.stdout = stdout or sys.stdout
        self.stdin = stdin or sys.stdin
        self.json_output = json_output
        self.is_tty = bool(getattr(self.stdout, "isatty", lambda: False)())
        self.input_is_tty = bool(getattr(self.stdin, "isatty", lambda: False)())
        self.rich = bool(
            _RICH_AVAILABLE
            and not json_output
            and not force_plain
            and self.is_tty
        )
        self.console = Console(file=self.stdout) if self.rich and Console else None

    def print(self, message: str = "") -> None:
        if self.console:
            self.console.print(message)
        else:
            print(message, file=self.stdout)

    def rule(self, title: str) -> None:
        if self.console:
            self.console.rule(title)
        else:
            self.print(f"\n{title}\n{'=' * len(title)}")

    def info(self, message: str) -> None:
        self.print(f"[info] {message}" if not self.console else f"[cyan]{message}[/cyan]")

    def success(self, message: str) -> None:
        self.print(f"[ok] {message}" if not self.console else f"[green]{message}[/green]")

    def warning(self, message: str) -> None:
        self.print(f"[warn] {message}" if not self.console else f"[yellow]{message}[/yellow]")

    def error(self, message: str) -> None:
        self.print(f"[error] {message}" if not self.console else f"[red]{message}[/red]")

    def panel(self, title: str, lines: Iterable[str]) -> None:
        body = "\n".join(str(line) for line in lines if str(line) != "")
        if self.console and Panel:
            self.console.print(Panel(body, title=title, border_style="cyan"))
        else:
            self.rule(title)
            if body:
                self.print(body)

    def hero(self, title: str, subtitle: str, rows: Iterable[tuple[str, object]]) -> None:
        rows_list = [(str(key), "" if value is None else str(value)) for key, value in rows]
        if self.console and Panel and Table:
            body = Table.grid(expand=True)
            body.add_column(ratio=1, style="bold cyan")
            body.add_column(ratio=3)
            body.add_row(f"[bold]{title}[/bold]", subtitle)
            body.add_row("", "")
            for key, value in rows_list:
                body.add_row(key, value)
            self.console.print(Panel(body, border_style="cyan", padding=(1, 2)))
            return

        self.panel(title, [subtitle, "", *[f"{key}: {value}" for key, value in rows_list]])

    def cards(self, title: str, cards: Iterable[tuple[str, str, str]]) -> None:
        cards_list = [(str(name), str(detail), str(command)) for name, detail, command in cards]
        if self.console and Panel and Columns:
            panels = [
                Panel(
                    f"{detail}\n\n[bold cyan]{command}[/bold cyan]",
                    title=name,
                    border_style="bright_black",
                    padding=(1, 2),
                )
                for name, detail, command in cards_list
            ]
            self.console.print(f"\n[bold]{title}[/bold]")
            self.console.print(Columns(panels, equal=True, expand=True))
            return

        self.table(title, ["goal", "command"], [(name, command) for name, _detail, command in cards_list])

    def choice_grid(self, title: str, options: Sequence[str]) -> None:
        if self.console and Panel and Table:
            table = Table.grid(expand=True)
            table.add_column(ratio=1)
            table.add_column(ratio=1)
            for idx in range(0, len(options), 2):
                left = f"[cyan]{idx + 1:>2}[/cyan]  {options[idx]}"
                right = ""
                if idx + 1 < len(options):
                    right = f"[cyan]{idx + 2:>2}[/cyan]  {options[idx + 1]}"
                table.add_row(left, right)
            table.add_row("[cyan] o[/cyan]  Other / custom", "")
            self.console.print(Panel(table, title=title, border_style="bright_black"))
            return

        self.print(f"\n{title}")
        for idx, option in enumerate(options, 1):
            self.print(f"  [{idx}] {option}")
        self.print("  [o] Other / custom")

    def table(
        self,
        title: str,
        columns: Sequence[str],
        rows: Iterable[Sequence[object]],
    ) -> None:
        rows_list = [tuple("" if cell is None else str(cell) for cell in row) for row in rows]
        if self.console and Table:
            table = Table(title=title, box=box.SIMPLE if box else None)
            for column in columns:
                table.add_column(column)
            for row in rows_list:
                table.add_row(*row)
            self.console.print(table)
            return

        self.rule(title)
        self.print("  ".join(columns))
        self.print("  ".join("-" * len(column) for column in columns))
        for row in rows_list:
            self.print("  ".join(row))

    def prompt(self, label: str, *, default: str = "") -> str:
        if self.console and Prompt and self.input_is_tty:
            return Prompt.ask(label, default=default)
        suffix = f" [{default}]" if default else ""
        value = input(f"{label}{suffix}: ").strip()
        return value or default

    def confirm(self, label: str, *, default: bool = False) -> bool:
        if self.console and Confirm and self.input_is_tty:
            return bool(Confirm.ask(label, default=default))
        suffix = "Y/n" if default else "y/N"
        value = input(f"{label} [{suffix}]: ").strip().lower()
        if not value:
            return default
        return value in {"y", "yes", "1", "true"}

    @contextmanager
    def status(self, message: str):
        if self.console:
            with self.console.status(message, spinner="dots"):
                yield
            return
        if self.is_tty and not self.json_output:
            self.print(message)
        yield
