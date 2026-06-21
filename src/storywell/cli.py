from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.columns import Columns
from rich.console import Console
from rich.table import Table

from . import __version__, service
from .models import WRITABLE_SHELVES, Shelf, SourceBook
from .sources import SourceError, available_sources
from .stats import compute_all, load_export

if TYPE_CHECKING:
    from .storygraph import SyncPlanItem
    from .storygraph.matching import Candidate, ScoredCandidate

HEADLESS_HELP = "Run the browser without a visible window; use --headed to watch or debug."

DEFAULT_SOURCE = "audible"

app = typer.Typer(
    add_completion=False,
    help="Sync finished books from Audible, Goodreads & more into The StoryGraph.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
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
FileOption = typer.Option(
    None,
    "--file",
    "-f",
    help="File sources (goodreads, librarything, kobo, calibre): path to the export CSV / "
    "database / Calibre library.",
)
TokenOption = typer.Option(
    None,
    "--token",
    help="API sources (hardcover): personal API token.",
)
ReadColumnOption = typer.Option(
    None,
    "--read-column",
    help="Calibre only: label of the custom column that tracks read status (required for "
    "calibre; Calibre has no built-in read field).",
)
_SHELF_CHOICES = ", ".join(s.value for s in WRITABLE_SHELVES)
ShelfOption = typer.Option(
    None,
    "--shelf",
    help="Route a source's books with no finished signal to this StoryGraph shelf "
    f"({_SHELF_CHOICES}). Finished books always go to 'read'. Omit to stay read-only "
    "(read-trackers are unaffected). For catalogue sources like LibraryThing this routes "
    "the whole catalogue; '--shelf read' is the modern spelling of --as-read.",
)
AsReadOption = typer.Option(
    False,
    "--as-read",
    help="LibraryThing only: legacy alias for '--shelf read' (treat every catalogued book "
    "as read).",
)
ReadDateOption = typer.Option(
    False,
    "--read-date",
    help="LibraryThing only: stamp today's date as the finish date for read books that "
    "have no Date Read.",
)


CollectionOption = typer.Option(
    None,
    "--collection",
    help="LibraryThing only: only import books in this collection (repeatable; omit for all).",
)


def _parse_shelf(value: str | None, *, as_read: bool) -> Shelf | None:
    """Resolve the requested target shelf from --shelf (preferred) or the legacy --as-read.

    Returns None when neither was given (read-only). Rejects an unknown or non-writable shelf
    so a typo fails loudly instead of silently doing nothing."""
    if value is None:
        return Shelf.READ if as_read else None
    try:
        shelf = Shelf(value.strip().lower())
    except ValueError:
        shelf = None
    if shelf not in WRITABLE_SHELVES:
        console.print(f"Unknown shelf '{value}'. Choose one of: {_SHELF_CHOICES}.", style="red")
        raise typer.Exit(code=1)
    return shelf


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"storywell {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
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
    auth_file: Path | None = None,
    profile: str | None = None,
    path: Path | None = None,
    token: str | None = None,
    shelf: Shelf | None = None,
    read_date: bool = False,
    collections: tuple[str, ...] = (),
    read_column: str | None = None,
) -> list[SourceBook]:
    return service.list_finished(
        source,
        threshold=threshold,
        auth_file=auth_file,
        profile=profile,
        path=path,
        token=token,
        shelf=shelf,
        read_date=read_date,
        collections=collections,
        read_column=read_column,
    )


@contextlib.contextmanager
def _session_browser(*, headless: bool) -> Iterator:
    """Open one authenticated StoryGraph browser, or exit with a clear message.

    Thin terminal-facing wrapper over ``service.session_browser``: it converts the
    service's domain errors (missing Playwright, no active session) into a red message
    and a non-zero exit, keeping the command bodies free of presentation concerns.
    """
    from .storygraph import StorygraphDependencyError

    try:
        with service.session_browser(headless=headless) as browser:
            yield browser
    except (StorygraphDependencyError, service.NotAuthenticatedError) as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err


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


@app.command("list")
def list_books(
    source: str = SourceOption,
    threshold: float = ThresholdOption,
    auth_file: Path | None = AuthFileOption,
    profile: str | None = ProfileOption,
    file: Path | None = FileOption,
    token: str | None = TokenOption,
    shelf: str | None = ShelfOption,
    as_read: bool = AsReadOption,
    read_date: bool = ReadDateOption,
    collection: list[str] = CollectionOption,
    read_column: str | None = ReadColumnOption,
) -> None:
    """List the books a source reports for syncing."""
    target = _parse_shelf(shelf, as_read=as_read)
    try:
        books = _load_finished(
            source,
            threshold=threshold,
            auth_file=auth_file,
            profile=profile,
            path=file,
            token=token,
            shelf=target,
            read_date=read_date,
            collections=tuple(collection or ()),
            read_column=read_column,
        )
    except SourceError as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err

    if not books:
        console.print(f"No finished books found from {source}.", style="yellow")
        return

    console.print(build_table(books, source=source))


StatsFileOption = typer.Option(
    ...,
    "--file",
    "-f",
    exists=True,
    dir_okay=False,
    readable=True,
    help="Path to your StoryGraph library CSV export "
    "(Account → Manage Account → Export StoryGraph Library).",
)
StatsHtmlOption = typer.Option(
    None,
    "--html",
    dir_okay=False,
    writable=True,
    help="Write a self-contained HTML dashboard to this path instead of printing a summary.",
)
StatsOpenOption = typer.Option(
    False,
    "--open",
    help="Open the written HTML dashboard in your browser (requires --html).",
)


def _pairs_table(
    title: str, label_header: str, pairs: list, *, value_header: str = "Count"
) -> Table:
    table = Table(title=title, show_lines=False, title_justify="left")
    table.add_column(label_header, overflow="fold")
    table.add_column(value_header, justify="right")
    for label, value in pairs:
        table.add_row(str(label), str(value))
    return table


def render_stats_summary(data: dict) -> None:
    """Print a Rich summary of a computed stats blob. Pure formatting over ``compute_all``."""
    summary = data["summary"]
    console.rule("StoryGraph reading stats")
    overview = Table.grid(padding=(0, 2))
    overview.add_column(style="bold")
    overview.add_column()
    overview.add_row("Books read", str(summary["read_books"]))
    overview.add_row("Total finishes (incl. re-reads)", str(summary["total_finishes"]))
    if summary["undated_reads"]:
        overview.add_row("Read but undated", str(summary["undated_reads"]))
    overview.add_row("Rated books", str(summary["rated_books"]))
    overview.add_row(
        "Average rating",
        "—" if summary["mean_rating"] is None else f"{summary['mean_rating']:.2f} ★",
    )
    if summary["latest_year"] is not None:
        overview.add_row(f"Finished in {summary['latest_year']}", str(summary["latest_year_books"]))
    console.print(overview)

    pace = data["volume_pace"]["reading_pace"]
    if pace["count"]:
        console.print(
            f"\nReading pace: median [bold]{pace['median_days']:g}[/] days to finish "
            f"(from {pace['count']} dated reads). "
            f"Fastest: {pace['shortest']['title']} ({pace['shortest']['days']}d); "
            f"slowest: {pace['longest']['title']} ({pace['longest']['days']}d)."
        )

    tables = [
        _pairs_table("Top formats", "Format", data["formats_authors"]["format_split"]),
        _pairs_table("Top moods", "Mood", data["moods_taste"]["mood_frequency"][:8]),
        _pairs_table(
            "Top authors",
            "Author",
            data["formats_authors"]["top_authors"][:8],
            value_header="Books",
        ),
    ]
    console.print("")
    console.print(Columns(tables, equal=True, expand=True))


@app.command("stats")
def stats(
    file: Path = StatsFileOption,
    as_json: bool = typer.Option(
        False, "--json", help="Print the full stats blob as JSON instead of a summary."
    ),
    html: Path | None = StatsHtmlOption,
    open_browser: bool = StatsOpenOption,
) -> None:
    """Analyse a StoryGraph library export into reading stats (offline, read-only)."""
    if open_browser and html is None:
        console.print("--open needs --html PATH (there's no dashboard to open).", style="red")
        raise typer.Exit(code=1)
    try:
        entries = load_export(file)
    except SourceError as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err

    data = compute_all(entries)
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    summary = data["summary"]
    if summary["read_books"] and not summary["total_finishes"]:
        console.print(
            "Found read books but no readable finish dates — your export's date format may be "
            "unexpected, so per-year and pace stats will be empty. Please report this.",
            style="yellow",
        )
    if html is not None:
        from .stats.render import StatsDependencyError, write_dashboard

        try:
            out = write_dashboard(data, html)
        except StatsDependencyError as err:
            console.print(str(err), style="red")
            raise typer.Exit(code=1) from err
        console.print(f"Wrote dashboard to {out}", style="green")
        if open_browser:
            import webbrowser

            webbrowser.open(out.resolve().as_uri())
        return
    render_stats_summary(data)


def build_match_table(items: list[SyncPlanItem]) -> Table:
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


def _choose_candidate(choice: str, options: list[ScoredCandidate]) -> Candidate | None:
    choice = (choice or "").strip().lower()
    if choice.isdigit():
        index = int(choice) - 1
        if 0 <= index < len(options):
            return options[index].candidate
    return None


def _display_name(item) -> str:
    authors = getattr(item, "authors", None)
    if authors:
        return f"{item.title} by {', '.join(authors)}"
    return getattr(item, "title", str(item))


def _prompt_ambiguous(item, result):
    options = [scored for scored in (result.best, *result.alternatives) if scored is not None]
    console.print(f"\nAmbiguous: [bold]{_display_name(item)}[/]", style="yellow")
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
    file: Path | None = FileOption,
    token: str | None = TokenOption,
    shelf: str | None = ShelfOption,
    as_read: bool = AsReadOption,
    read_date: bool = ReadDateOption,
    collection: list[str] = CollectionOption,
    read_column: str | None = ReadColumnOption,
    dry_run: bool = typer.Option(
        False, "--dry-run/--no-dry-run", help="Preview matches without writing to StoryGraph."
    ),
    limit: int = typer.Option(
        0, "--limit", min=0, help="Process at most N finished books (0 = all)."
    ),
    ratings: bool = typer.Option(
        True, "--ratings/--no-ratings", help="Also sync your rating + review (with narrator note)."
    ),
    headless: bool = typer.Option(True, "--headless/--headed", help=HEADLESS_HELP),
) -> None:
    """Sync a source's books to StoryGraph shelves (and sync ratings/reviews).

    Finished books are marked read; with --shelf, books that have no finished signal are
    routed to the chosen shelf instead (default: read-only, so read-trackers are unaffected).
    Writes high-confidence matches directly and prompts on ambiguous ones.
    Use --dry-run to preview the match plan without writing anything.
    """
    from .config import sync_store_path
    from .storygraph import (
        MatchStatus,
        StorygraphAuthError,
        StorygraphDependencyError,
        SyncStore,
        run_review_sync,
        run_sync,
        summarize,
    )
    from .storygraph.client import StorygraphClient
    from .storygraph.search import StorygraphSearcher

    target = _parse_shelf(shelf, as_read=as_read)
    try:
        books = _load_finished(
            source,
            threshold=threshold,
            auth_file=auth_file,
            profile=profile,
            path=file,
            token=token,
            shelf=target,
            read_date=read_date,
            collections=tuple(collection or ()),
            read_column=read_column,
        )
    except SourceError as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err

    if not books:
        console.print(f"No books to sync from {source}.", style="yellow")
        return

    if limit:
        books = books[:limit]

    if dry_run:
        try:
            items = service.build_sync_plan(books, headless=headless)
        except (
            StorygraphDependencyError,
            service.NotAuthenticatedError,
            StorygraphAuthError,
        ) as err:
            console.print(str(err), style="red")
            raise typer.Exit(code=1) from err
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
    outcome = None
    review_outcome = None
    try:
        with _session_browser(headless=headless) as browser:
            searcher = StorygraphSearcher(page=browser.page)
            client = StorygraphClient(page=browser.page)
            with searcher, client:
                outcome = run_sync(
                    books,
                    search_fn=searcher.search,
                    writer=client,
                    store=store,
                    confirm_fn=_prompt_ambiguous,
                    edition_fn=searcher.resolve_edition,
                    read_elsewhere_fn=searcher.read_on_another_edition,
                    default_shelf=target,
                )
                review_outcome = (
                    run_review_sync(books, rater=client, store=store) if ratings else None
                )
    except StorygraphAuthError as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err
    finally:
        store.save()  # persist progress even if the run was interrupted mid-way

    console.print(
        f"read — written: {len(outcome.written)}  "
        f"skipped (already synced): {len(outcome.skipped_synced)}  "
        f"skipped (read on another edition): {len(outcome.skipped_other_edition)}  "
        f"ambiguous skipped: {len(outcome.ambiguous_skipped)}  "
        f"no match: {len(outcome.no_match)}  "
        f"failed: {len(outcome.failed)}",
        style="cyan",
    )
    if review_outcome is not None:
        console.print(
            f"ratings/reviews — written: {len(review_outcome.written)}  "
            f"skipped: {len(review_outcome.skipped_synced)}  "
            f"no match: {len(review_outcome.no_match)}  "
            f"failed: {len(review_outcome.failed)}",
            style="cyan",
        )
    if outcome.failed:
        console.print(
            f"{len(outcome.failed)} book(s) failed to write; re-run to retry.", style="red"
        )


_RETAG_STYLE = {
    "retaggable": "yellow",
    "already_audio": "green",
    "no_audio_edition": "red",
    "unknown": "dim",
}


def build_retag_table(items: list, titles: dict[str, str]) -> Table:
    table = Table(title=f"Audio-edition retag report ({len(items)})", show_lines=False)
    table.add_column("Title", overflow="fold")
    table.add_column("Status")
    table.add_column("Current edition")
    table.add_column("Audio edition")
    for item in items:
        style = _RETAG_STYLE.get(item.status, "white")
        table.add_row(
            titles.get(item.key, item.key),
            f"[{style}]{item.status}[/]",
            f"{item.current_format or '?'} ({item.current_id})",
            item.audio_id or "-",
        )
    return table


@app.command("retag")
def retag(
    source: str = SourceOption,
    threshold: float = ThresholdOption,
    auth_file: Path | None = AuthFileOption,
    profile: str | None = ProfileOption,
    limit: int = typer.Option(
        0, "--limit", min=0, help="Inspect at most N matched books (0 = all)."
    ),
    headless: bool = typer.Option(True, "--headless/--headed"),
) -> None:
    """Report which already-synced audiobooks are marked on a non-audio StoryGraph edition.

    Read-only: inspects each matched book's editions and shows whether it is already on the
    audio edition, could be moved to one, or has no audio edition. Writing the moves is not
    implemented yet — this is the diagnostic that sizes that work.
    """
    from rich.progress import track

    from .config import sync_store_path
    from .storygraph import (
        StorygraphBrowser,
        StorygraphDependencyError,
        SyncStore,
        is_authenticated,
        plan_retag,
    )
    from .storygraph.search import StorygraphSearcher

    try:
        books = _load_finished(source, threshold=threshold, auth_file=auth_file, profile=profile)
    except SourceError as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err

    store = SyncStore.load(sync_store_path())
    matched = [book for book in books if store.cached_book_id(book.key) is not None]
    if limit:
        matched = matched[:limit]
    if not matched:
        console.print(f"No matched {source} books in the sync store yet.", style="yellow")
        return

    try:
        if not is_authenticated():
            console.print(service.NO_SESSION_MESSAGE, style="red")
            raise typer.Exit(code=1)
    except StorygraphDependencyError as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err

    titles = {book.key: book.title for book in matched}
    with StorygraphBrowser(headless=headless) as browser:
        searcher = StorygraphSearcher(page=browser.page)
        with searcher:
            items = plan_retag(
                track(matched, description="Checking editions"),
                store=store,
                editions_fn=searcher.list_editions,
            )

    console.print(build_retag_table(items, titles))
    counts: dict[str, int] = {}
    for item in items:
        counts[item.status] = counts.get(item.status, 0) + 1
    console.print(
        f"retaggable: {counts.get('retaggable', 0)}  "
        f"already audio: {counts.get('already_audio', 0)}  "
        f"no audio edition: {counts.get('no_audio_edition', 0)}  "
        f"unknown: {counts.get('unknown', 0)}",
        style="cyan",
    )
    console.print(
        f"{counts.get('retaggable', 0)} of {len(items)} matched books are on a non-audio "
        "edition with an audio edition available to move them to.",
        style="cyan",
    )


@app.command("collections")
def collections(
    source: str = SourceOption,
    threshold: float = ThresholdOption,
    auth_file: Path | None = AuthFileOption,
    profile: str | None = ProfileOption,
    file: Path | None = FileOption,
    token: str | None = TokenOption,
    dry_run: bool = typer.Option(
        True, "--dry-run/--no-dry-run", help="Preview detected collections; no writes."
    ),
    headless: bool = typer.Option(True, "--headless/--headed", help=HEADLESS_HELP),
) -> None:
    """Mark the books contained in finished collections as read on StoryGraph.

    --dry-run (default) previews each collection and its proposed contained titles;
    --no-dry-run prompts you to pick which to mark read (with the collection's finish date).
    """
    from .config import sync_store_path
    from .storygraph import StorygraphAuthError, SyncStore, run_title_sync
    from .storygraph.client import StorygraphClient
    from .storygraph.collections import proposed_titles, select_titles
    from .storygraph.matching import search_title
    from .storygraph.search import StorygraphSearcher
    from .storygraph.sync import TitleEntry

    try:
        books = _load_finished(
            source,
            threshold=threshold,
            auth_file=auth_file,
            profile=profile,
            path=file,
            token=token,
        )
    except SourceError as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err

    found = [book for book in books if book.is_collection]
    if not found:
        console.print(f"No finished collections found from {source}.", style="yellow")
        return

    if dry_run:
        try:
            with _session_browser(headless=headless) as browser:
                searcher = StorygraphSearcher(page=browser.page)
                for book in found:
                    candidates = searcher.search(search_title(book.title))
                    best = candidates[0] if candidates else None
                    titles = (
                        proposed_titles(best.title, searcher.fetch_description(best.book_id))
                        if best
                        else []
                    )
                    console.print(f"\n[bold]{book.title}[/]", style="cyan")
                    console.print(f"  StoryGraph: {best.title if best else '— not found —'}")
                    for title in titles:
                        console.print(f"    [ ] {title}")
                    if not titles:
                        console.print(
                            "    (no titles parsed — type them manually with --no-dry-run)"
                        )
        except StorygraphAuthError as err:
            console.print(str(err), style="red")
            raise typer.Exit(code=1) from err
        return

    store = SyncStore.load(sync_store_path())
    totals = {"written": 0, "skipped": 0, "failed": 0}
    try:
        with _session_browser(headless=headless) as browser:
            searcher = StorygraphSearcher(page=browser.page)
            client = StorygraphClient(page=browser.page)
            with searcher, client:
                for book in found:
                    finish_date = book.finished_at.date() if book.finished_at else None
                    candidates = searcher.search(search_title(book.title))
                    best = candidates[0] if candidates else None
                    suggestions = (
                        proposed_titles(best.title, searcher.fetch_description(best.book_id))
                        if best
                        else []
                    )
                    console.print(f"\n[bold]{book.title}[/]", style="cyan")
                    for number, title in enumerate(suggestions, 1):
                        console.print(f"  {number}. {title}")
                    console.print("(enter numbers like 1,3,5 — 'a' for all, blank to skip)")
                    chosen = select_titles(suggestions, typer.prompt("Mark which", default=""))
                    extra = typer.prompt("Add other titles (comma-separated)", default="")
                    chosen += [t.strip() for t in extra.split(",") if t.strip()]
                    if not chosen:
                        continue
                    entries = [
                        TitleEntry(
                            key=f"{book.key}::{t.lower()}",
                            title=t,
                            finish_date=finish_date,
                            media_format=book.media_format,
                        )
                        for t in chosen
                    ]
                    outcome = run_title_sync(
                        entries,
                        search_fn=searcher.search,
                        writer=client,
                        store=store,
                        confirm_fn=_prompt_ambiguous,
                        edition_fn=searcher.resolve_edition,
                    )
                    totals["written"] += len(outcome.written)
                    totals["skipped"] += (
                        len(outcome.skipped_synced)
                        + len(outcome.no_match)
                        + len(outcome.ambiguous_skipped)
                    )
                    totals["failed"] += len(outcome.failed)
    except StorygraphAuthError as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err
    finally:
        store.save()
    console.print(
        f"\nwritten: {totals['written']}  skipped: {totals['skipped']}  failed: {totals['failed']}",
        style="cyan",
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


@app.command("storygraph-install")
def storygraph_install() -> None:
    """Download the Chromium browser StoryGraph sync needs (one-time, no terminal needed later)."""
    from .storygraph import StorygraphDependencyError, chromium_installed, install_chromium

    try:
        if chromium_installed():
            console.print("Chromium is already installed.", style="green")
            return
        console.print("Downloading Chromium (~150 MB); this can take a minute…", style="cyan")
        ok = install_chromium()
    except StorygraphDependencyError as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err

    if ok:
        console.print("Chromium installed.", style="green")
    else:
        console.print("Chromium download failed. Check your connection and try again.", style="red")
        raise typer.Exit(code=1)


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
        console.print(service.NO_SESSION_MESSAGE, style="yellow")
        raise typer.Exit(code=1)


@app.command("audible-login")
def audible_login(
    marketplace: str = typer.Option(
        "us",
        "--marketplace",
        "-m",
        help="Audible marketplace country code (us, uk, de, ca, au, fr, jp, it, in, es, br).",
    ),
) -> None:
    """Log in to Audible in a browser and save the session (no `audible quickstart` needed)."""
    from .sources.audible_auth import AudibleLoginError
    from .sources.audible_auth import audible_login as run_login

    console.print("Opening a browser. Log in to Amazon/Audible, then return here.", style="cyan")
    try:
        path = run_login(marketplace)
    except AudibleLoginError as err:
        console.print(str(err), style="red")
        raise typer.Exit(code=1) from err

    console.print(f"Saved Audible login to {path}", style="green")


if __name__ == "__main__":
    app()
