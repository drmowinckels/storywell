from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .audible_client import AuthFileNotFound, LibraryFetchError, finished_audiobooks
from .models import Audiobook

app = typer.Typer(
    add_completion=False,
    help="Sync finished audiobooks from Audible to The StoryGraph.",
    no_args_is_help=True,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"audible-storygraph-sync {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    pass


def build_table(books: list[Audiobook]) -> Table:
    table = Table(title=f"Finished audiobooks ({len(books)})", show_lines=False)
    table.add_column("Title", overflow="fold")
    table.add_column("Author")
    table.add_column("%", justify="right")
    table.add_column("Finished")

    def sort_key(book: Audiobook) -> tuple[str, str]:
        return (book.finished_at.isoformat() if book.finished_at else "", book.title.lower())

    for book in sorted(books, key=sort_key):
        table.add_row(
            book.title,
            ", ".join(book.authors),
            f"{book.percent_complete:.0f}",
            book.finished_at.date().isoformat() if book.finished_at else "-",
        )
    return table


@app.command("audible-list")
def audible_list(
    threshold: float = typer.Option(
        0.95,
        "--threshold",
        "-t",
        min=0.0,
        max=1.0,
        help="Treat a book as finished when percent_complete >= threshold.",
    ),
    auth_file: Path | None = typer.Option(
        None,
        "--auth-file",
        help=(
            "Path to the audible-cli auth JSON. "
            "Auto-discovered via ~/.audible/config.toml if omitted."
        ),
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="audible-cli profile name. Defaults to the primary profile in config.toml.",
    ),
) -> None:
    """List your finished Audible audiobooks."""
    try:
        books = finished_audiobooks(threshold=threshold, auth_file=auth_file, profile=profile)
    except (AuthFileNotFound, LibraryFetchError) as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err

    if not books:
        console.print("No finished audiobooks found.", style="yellow")
        return

    console.print(build_table(books))


@app.command("storygraph-login")
def storygraph_login() -> None:
    """Open a browser to log in to StoryGraph and save the session for syncing."""
    from .storygraph import StorygraphAuthError, StorygraphDependencyError, login

    console.print("Opening a browser. Log in to StoryGraph, then return here.", style="cyan")
    try:
        path = login()
    except (StorygraphDependencyError, StorygraphAuthError) as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err

    console.print(f"Saved StoryGraph session to {path}", style="green")


@app.command("storygraph-status")
def storygraph_status() -> None:
    """Check whether a saved StoryGraph session is still logged in."""
    from .storygraph import StorygraphDependencyError, is_authenticated

    try:
        ok = is_authenticated()
    except StorygraphDependencyError as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err

    if ok:
        console.print("StoryGraph session is active.", style="green")
    else:
        console.print("No active StoryGraph session. Run `storygraph-login`.", style="yellow")
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
