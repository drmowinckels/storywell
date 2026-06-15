from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .models import SourceBook
from .sources import SourceError, available_sources, make_source

DEFAULT_SOURCE = "audible"

app = typer.Typer(
    add_completion=False,
    help="Sync finished books from Audible, Goodreads & more into The StoryGraph.",
    no_args_is_help=True,
)
console = Console()

SourceOption = typer.Option(
    DEFAULT_SOURCE,
    "--source",
    "-s",
    help="Which vendor to read from. See `storywell sources`.",
)
ThresholdOption = typer.Option(
    0.95,
    "--threshold",
    "-t",
    min=0.0,
    max=1.0,
    help="Treat a book as finished when percent_complete >= threshold.",
)
AuthFileOption = typer.Option(
    None,
    "--auth-file",
    help="Audible only: path to the audible-cli auth JSON. Auto-discovered if omitted.",
)
ProfileOption = typer.Option(
    None,
    "--profile",
    help="Audible only: audible-cli profile name. Defaults to the primary profile.",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"storywell {__version__}")
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


def _load_finished(
    source: str,
    *,
    threshold: float,
    auth_file: Path | None,
    profile: str | None,
) -> list[SourceBook]:
    src = make_source(source, auth_file=auth_file, profile=profile)
    return src.finished_books(threshold=threshold)


def build_table(books: list[SourceBook], *, source: str) -> Table:
    table = Table(title=f"Finished books from {source} ({len(books)})", show_lines=False)
    table.add_column("Title", overflow="fold")
    table.add_column("Author")
    table.add_column("%", justify="right")
    table.add_column("Finished")

    def sort_key(book: SourceBook) -> tuple[str, str]:
        return (book.finished_at.isoformat() if book.finished_at else "", book.title.lower())

    for book in sorted(books, key=sort_key):
        table.add_row(
            book.title,
            ", ".join(book.authors),
            f"{book.percent_complete:.0f}",
            book.finished_at.date().isoformat() if book.finished_at else "-",
        )
    return table


@app.command("sources")
def sources() -> None:
    """List the vendors Storywell can read from."""
    for name in available_sources():
        console.print(f"- {name}")


@app.command("migrate-store")
def migrate_store() -> None:
    """Carry over a pre-rename audible-storygraph-sync session and sync history."""
    from .migrate import migrate_legacy

    report = migrate_legacy()
    if not (report.state_migrated or report.store_migrated):
        console.print("Nothing to migrate (no legacy data, or already migrated).", style="yellow")
        return
    if report.state_migrated:
        console.print("Migrated saved StoryGraph session.", style="green")
    if report.store_migrated:
        console.print(
            f"Migrated sync history: {report.mappings} matches, {report.synced} synced.",
            style="green",
        )


@app.command("list")
def list_books(
    source: str = SourceOption,
    threshold: float = ThresholdOption,
    auth_file: Path | None = AuthFileOption,
    profile: str | None = ProfileOption,
) -> None:
    """List the finished books a source reports."""
    try:
        books = _load_finished(source, threshold=threshold, auth_file=auth_file, profile=profile)
    except SourceError as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err

    if not books:
        console.print(f"No finished books found from {source}.", style="yellow")
        return

    console.print(build_table(books, source=source))


def build_match_table(items: list) -> Table:
    from .storygraph import MatchStatus

    style = {
        MatchStatus.MATCH: "green",
        MatchStatus.AMBIGUOUS: "yellow",
        MatchStatus.NO_MATCH: "red",
    }
    table = Table(title=f"StoryGraph match plan ({len(items)})", show_lines=False)
    table.add_column("Source title", overflow="fold")
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


def _prompt_ambiguous(book: SourceBook, result):
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
    source: str = SourceOption,
    threshold: float = ThresholdOption,
    auth_file: Path | None = AuthFileOption,
    profile: str | None = ProfileOption,
    dry_run: bool = typer.Option(
        False, "--dry-run/--no-dry-run", help="Preview matches without writing to StoryGraph."
    ),
    limit: int = typer.Option(
        0, "--limit", min=0, help="Process at most N finished books (0 = all)."
    ),
    headless: bool = typer.Option(True, "--headless/--headed"),
) -> None:
    """Mark a source's finished books as read on StoryGraph.

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
        books = _load_finished(source, threshold=threshold, auth_file=auth_file, profile=profile)
    except SourceError as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err

    if not books:
        console.print(f"No finished books found from {source}.", style="yellow")
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
