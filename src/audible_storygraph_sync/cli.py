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


def build_match_table(items: list) -> Table:
    from .storygraph import MatchStatus

    style = {
        MatchStatus.MATCH: "green",
        MatchStatus.AMBIGUOUS: "yellow",
        MatchStatus.NO_MATCH: "red",
    }
    table = Table(title=f"StoryGraph match plan ({len(items)})", show_lines=False)
    table.add_column("Audible title", overflow="fold")
    table.add_column("Status")
    table.add_column("StoryGraph match", overflow="fold")
    table.add_column("Score", justify="right")

    for item in items:
        result = item.result
        best = result.best
        match_text = f"{best.candidate.title} ({best.candidate.book_id})" if best else "-"
        score_text = f"{best.score:.2f}" if best else "-"
        table.add_row(
            item.book.title,
            f"[{style[result.status]}]{result.status.value}[/]",
            match_text,
            score_text,
        )
    return table


def _choose_candidate(choice: str, options: list):
    choice = (choice or "").strip().lower()
    if choice.isdigit():
        index = int(choice) - 1
        if 0 <= index < len(options):
            return options[index].candidate
    return None


def _prompt_ambiguous(book: Audiobook, result):
    options = [scored for scored in (result.best, *result.alternatives) if scored is not None]
    authors = ", ".join(book.authors) or "?"
    console.print(f"\nAmbiguous: [bold]{book.title}[/] by {authors}", style="yellow")
    for number, scored in enumerate(options, 1):
        candidate = scored.candidate
        console.print(
            f"  {number}. {candidate.title} — {candidate.author or '?'}  "
            f"({scored.score:.2f})  [{candidate.book_id}]"
        )
    choice = typer.prompt("Pick a number to mark read, or 's' to skip", default="s")
    return _choose_candidate(choice, options)


@app.command("sync")
def sync(
    threshold: float = typer.Option(0.95, "--threshold", "-t", min=0.0, max=1.0),
    auth_file: Path | None = typer.Option(None, "--auth-file"),
    profile: str | None = typer.Option(None, "--profile"),
    dry_run: bool = typer.Option(
        False, "--dry-run/--no-dry-run", help="Preview matches without writing to StoryGraph."
    ),
    limit: int = typer.Option(
        0, "--limit", min=0, help="Process at most N finished books (0 = all)."
    ),
    headless: bool = typer.Option(True, "--headless/--headed"),
) -> None:
    """Mark finished Audible books as read on StoryGraph.

    Writes high-confidence matches directly and prompts on ambiguous ones.
    Use --dry-run to preview the match plan without writing anything.
    """
    from .config import sync_store_path
    from .storygraph import (
        MatchStatus,
        StorygraphBrowser,
        StorygraphDependencyError,
        SyncStore,
        is_authenticated,
        plan_sync,
        run_sync,
        summarize,
    )
    from .storygraph.client import StorygraphClient
    from .storygraph.search import StorygraphSearcher

    try:
        books = finished_audiobooks(threshold=threshold, auth_file=auth_file, profile=profile)
    except (AuthFileNotFound, LibraryFetchError) as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err

    if not books:
        console.print("No finished audiobooks found.", style="yellow")
        return

    if limit:
        books = books[:limit]

    try:
        if not is_authenticated():
            console.print("No active StoryGraph session. Run `storygraph-login`.", style="red")
            raise typer.Exit(code=1)
    except StorygraphDependencyError as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err

    if dry_run:
        with StorygraphSearcher(headless=headless) as searcher:
            items = plan_sync(books, searcher.search)
        console.print(build_match_table(items))
        counts = summarize(items)
        console.print(
            f"match: {counts[MatchStatus.MATCH]}  "
            f"ambiguous: {counts[MatchStatus.AMBIGUOUS]}  "
            f"no match: {counts[MatchStatus.NO_MATCH]}",
            style="cyan",
        )
        return

    store = SyncStore.load(sync_store_path())
    with StorygraphBrowser(headless=headless) as browser:
        searcher = StorygraphSearcher(page=browser.page)
        client = StorygraphClient(page=browser.page)
        with searcher, client:
            outcome = run_sync(
                books,
                search_fn=searcher.search,
                writer=client,
                store=store,
                confirm_fn=_prompt_ambiguous,
            )
    store.save()

    console.print(
        f"written: {len(outcome.written)}  "
        f"skipped (already synced): {len(outcome.skipped_synced)}  "
        f"ambiguous skipped: {len(outcome.ambiguous_skipped)}  "
        f"no match: {len(outcome.no_match)}  "
        f"failed: {len(outcome.failed)}",
        style="cyan",
    )
    if outcome.failed:
        console.print(
            f"{len(outcome.failed)} book(s) failed to write; re-run to retry.", style="red"
        )


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
