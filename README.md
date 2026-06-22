<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="img/submark-night.svg" />
    <img src="img/submark.svg" alt="Storywell" width="116" />
  </picture>
</p>

<h1 align="center">Storywell</h1>

<p align="center"><em>Sync the books you've finished — across every vendor — into <a href="https://app.thestorygraph.com">The StoryGraph</a>.</em></p>

<p align="center">
  <a href="https://drmowinckels.github.io/storywell/">Docs &amp; guide</a> ·
  <a href="https://drmowinckels.github.io/storywell/sources.html">Sources</a> ·
  <a href="https://drmowinckels.github.io/storywell/roadmap.html">Roadmap</a>
</p>

Storywell reads your finished books from a _source_ and marks them as read on StoryGraph, which has
no public import API. It matches each book to the right StoryGraph entry (by ISBN when a source
provides one, by title/author otherwise), writes the finish date, and optionally carries over your
rating and review. One `--source` flag selects the vendor; the same matching-and-writing engine
runs behind all of them.

> **Status:** v0.1 alpha. Audible → StoryGraph read sync is solid; Goodreads and Kobo are validated
> against real exports. LibraryThing and Hardcover parse correctly but are **experimental** (see
> [caveats](#sources)). Marking-as-read, finish dates, ratings/reviews, audio-edition tagging, and
> idempotent re-runs all work.

## Why this exists

The previous community tool, [`good_audible_story_sync`](https://github.com/cheshire137/good-audible-story-sync),
no longer works: StoryGraph put up a Cloudflare bot challenge that its Mechanize-based scraper cannot solve.
Storywell replaces it with a Python CLI that uses actively maintained libraries on the source side and a
real Chromium session via Playwright on the StoryGraph side, sidestepping Cloudflare entirely.

## Install

Storywell isn't on PyPI yet, so install it from source (Python 3.11 or 3.12):

```sh
git clone https://github.com/drmowinckels/storywell.git
cd storywell
python -m pip install -e ".[storygraph]"
```

StoryGraph write-back drives a real browser, so install Playwright's Chromium once:

```sh
storywell storygraph-install   # or, equivalently: playwright install chromium
```

Then log in once — Storywell saves the session and reuses it:

```sh
storywell storygraph-login
```

> Not yet on PyPI. Once released, `pipx install storywell` will be the one-line install.

## Usage

```sh
storywell sources                          # which vendors Storywell can read from
storywell list                             # list finished books (default source: audible)
storywell list --source goodreads -f export.csv
storywell audible-login                    # log in to Audible in a browser (no audible-cli needed)
storywell storygraph-install               # download the Chromium browser sync needs (one-time)
storywell storygraph-login                 # log in to StoryGraph once (saves a session)
storywell sync --dry-run                   # preview the StoryGraph match plan
storywell sync                             # mark finished books as read (+ ratings/reviews)
storywell collections                      # preview books contained in finished collections
storywell retag                            # report which synced books are on a non-audio edition
```

Common options:

- `--source, -s` — which vendor to read from (default `audible`).
- `--threshold, -t` — listening/reading-progress cutoff above which a book counts as finished
  (default `0.95`), in addition to anything the source itself flags complete.
- `--file, -f` — export/database path for file sources (goodreads, kobo, calibre, applebooks, bookwyrm, libby, kindle, librarything).
- `--token` — personal API token for `hardcover` (or set `HARDCOVER_TOKEN`).
- `--shelf <status>` — route books with no finished signal to a StoryGraph shelf (`read`,
  `currently-reading`, `to-read`, `did-not-finish`); finished books always go to `read`. Omit to
  stay read-only, so read-trackers are unaffected.
- `--as-read`, `--read-date`, `--collection` — LibraryThing-only knobs (it's a catalogue, not a
  read tracker); `--as-read` is the legacy alias for `--shelf read`. See [Sources](#sources).
- `--dry-run` (sync) / `--no-dry-run` (collections) — preview vs write.
- `--limit N`, `--no-ratings`, `--headed` — process at most N books, skip ratings/reviews, or watch
  the browser work.
- `--help, -h` — show help for the app or any command; `--version, -V` — print the installed version.

`sync` writes high-confidence matches directly, prompts you on ambiguous ones, and is idempotent: a
book is only (re)written if its finish date changed. Matches are keyed by `source:id`, so multiple
vendors never collide. Because an audiobook source is an audio source, Storywell marks the
**audiobook edition** on StoryGraph, falling back to the best-matching edition if a work has no
audio edition. `retag` is a read-only report (no writes) that sizes a back-fill of older matches
onto their audio edition; applying the moves is not implemented yet.

The full command and flag reference lives in the [docs](https://drmowinckels.github.io/storywell/usage.html).

## Sources

| Source         | Format | Input                                                                              | Flags                                                           |
| -------------- | ------ | ---------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `audible`      | audio  | `storywell audible-login` (or audible-cli's `audible quickstart`)                  | `--auth-file`, `--profile`                                      |
| `applebooks`   | ebook  | on-device `BKLibrary*.sqlite` (macOS, auto-detected)                               | `--file`                                                        |
| `bookwyrm`     | mixed  | CSV export (Settings → Export)                                                     | `--file`                                                        |
| `calibre`      | mixed  | local Calibre library `metadata.db`                                                | `--file`, `--read-column`                                       |
| `goodreads`    | mixed  | `goodreads_library_export.csv` (Settings → Export)                                 | `--file`                                                        |
| `hardcover`    | mixed  | GraphQL API token ([hardcover.app/account/api](https://hardcover.app/account/api)) | `--token`                                                       |
| `kindle`       | ebook  | Amazon "Request My Data" export (CSVs)                                             | `--file`                                                        |
| `kobo`         | ebook  | on-device `KoboReader.sqlite`                                                      | `--file`                                                        |
| `libby`        | mixed  | Libby Timeline / OverDrive history CSV                                             | `--file`, `--shelf`                                             |
| `librarything` | mixed  | CSV/JSON export                                                                    | `--file`, `--shelf`, `--as-read`, `--read-date`, `--collection` |
| `literal`      | mixed  | GraphQL token (login)                                                              | `--token`                                                       |

The Audible source reads `~/.audible/config.toml` to find the active profile's auth file; pass
`--profile <name>` or `--auth-file <path>` to override discovery. Apple Books is macOS-only and
auto-detects its on-device library. Calibre has no built-in read field, so it needs
`--read-column LABEL` naming the custom column you track read status in (a Yes/No, rating, or text
column); point `--file` at the library folder or the `metadata.db` inside it. Literal and Hardcover
take an API `--token`; Bookwyrm reads its CSV export.

LibraryThing (a catalogue) and Libby (borrow history) carry no finished signal, so by default
nothing syncs. Route their books onto a shelf with `--shelf <status>` (e.g. `--shelf to-read`); a
real finished signal always wins and goes to `read`. `--read-date` stamps today on read books with
no date, and `--collection NAME` (LibraryThing, repeatable) scopes to a named collection like
"Read".

**Newly added sources — run `--dry-run` first:**

- **Literal, Apple Books, Calibre, Bookwyrm, Libby, Kindle** are new: they parse correctly against
  captured fixtures and follow the same patterns as the stable sources, but haven't been exercised
  against a broad range of real exports/libraries yet. Kindle's "finished" is _inferred_ from
  reading sessions (the export has no finished flag), so it's best-effort.
- **Hardcover** is not yet runtime-tested against a live token; "finished" relies on the documented
  `status_id == 3` ("Read") mapping.
- **LibraryThing** is validated against a single real export.

Cross-source de-duplication (the same book from two sources collapsing to one StoryGraph write) and
non-`read` shelf routing are implemented and verified.

Per-source setup and caveats are documented in full in the
[Sources guide](https://drmowinckels.github.io/storywell/sources.html). Don't see your service?
Storytel, Libro.fm, Hoopla, Spotify audiobooks, Google Play Books and others aren't supported yet —
the guide [explains which and why](https://drmowinckels.github.io/storywell/sources.html#unsupported).

## Roadmap

- [x] Audible → StoryGraph: list, match, mark-as-read with finish dates
- [x] Multi-source architecture (`--source`, pluggable provider registry, ISBN-first matching)
- [x] Goodreads, Kobo, Calibre sources
- [x] LibraryThing, Hardcover sources — _experimental_
- [x] Literal, Apple Books, Bookwyrm, Libby, Kindle sources — _newly added_
- [x] Ratings & reviews sync, audio-edition tagging
- [x] Cross-source de-duplication
- [x] Shelf routing — mark `to-read` / `currently-reading` / `did-not-finish`, not just `read`
- [ ] Harden the new sources against more real exports; live Hardcover token
- [ ] Evaluate Google Play Books and Spotify audiobooks (one feasibility test each)
- [ ] Audio-edition retag write side (today `retag` only reports)
- [ ] Homebrew tap, hosted UI

## Adding a source

A source is a class with a `name`, a `media_format` (`"audio"`, `"ebook"`, `"print"`, or `""` when
mixed/unknown), and a `finished_books()` method that returns `SourceBook`s
(see [`src/storywell/sources/base.py`](src/storywell/sources/base.py)). Register it in
[`src/storywell/sources/__init__.py`](src/storywell/sources/__init__.py) and it becomes available
under `--source <name>`. `make_source` passes through only the CLI options your constructor
declares, so one CLI surface carries the union of every source's options. The StoryGraph
matching/write side is source-agnostic; `media_format` is what tells it which edition to tag. CSV/TSV
vendors can subclass `CsvSource` and implement only `row_to_book`. Full walkthrough in the
[Adding a source guide](https://drmowinckels.github.io/storywell/adding-a-source.html).

## Development

```sh
git clone https://github.com/drmowinckels/storywell.git
cd storywell
make install
make check        # lint + format check + tests
```

The docs site lives in [`site/`](site/) (Quarto) and is published to GitHub Pages by
[`.github/workflows/site.yml`](.github/workflows/site.yml).

## License

MIT
