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
playwright install chromium
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
- `--file, -f` — export CSV / database path for file sources (goodreads, librarything, kobo).
- `--token` — personal API token for `hardcover` (or set `HARDCOVER_TOKEN`).
- `--as-read`, `--read-date`, `--collection` — LibraryThing-only knobs (it's a catalogue, not a
  read tracker); see [Sources](#sources).
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

| Source         | Format | Input                                                                              | Flags                                                |
| -------------- | ------ | ---------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `audible`      | audio  | `audible-cli` registration (`audible quickstart`)                                  | `--auth-file`, `--profile`                           |
| `goodreads`    | mixed  | `goodreads_library_export.csv` (Settings → Export)                                 | `--file`                                             |
| `kobo`         | ebook  | on-device `KoboReader.sqlite`                                                      | `--file`                                             |
| `librarything` | mixed  | CSV/JSON export                                                                    | `--file`, `--as-read`, `--read-date`, `--collection` |
| `hardcover`    | mixed  | GraphQL API token ([hardcover.app/account/api](https://hardcover.app/account/api)) | `--token`                                            |

The Audible source reads `~/.audible/config.toml` to find the active profile's auth file; pass
`--profile <name>` for a non-default profile or `--auth-file <path>` to bypass discovery.
LibraryThing is a catalogue, not a read tracker — by default nothing counts as read; `--as-read`
treats every catalogued book as read, `--read-date` stamps today's date, and `--collection NAME`
(repeatable) scopes to a named collection like "Read".

**Experimental sources — read before trusting a run:**

- **Hardcover** is **not yet runtime-tested**: the query/mapping match Hardcover's documented GraphQL
  schema and field access is defensive, but it hasn't been run against a live token, and "finished"
  relies on the documented `status_id == 3` ("Read") mapping. Verify a `--dry-run` first.
- **Goodreads** parsing is validated (1,156 rows across three exports, ~90% ISBN coverage), but the
  cross-source **de-duplication premise is unverified** against a real mixed-source run, and many
  `read` rows have no Date Read (marked read without a finish date).
- **LibraryThing** is validated against a **single** real export; column fallbacks cover older
  layouts but it hasn't been exercised on more exports.

Per-source setup and caveats are documented in full in the
[Sources guide](https://drmowinckels.github.io/storywell/sources.html).

## Roadmap

- [x] Audible → StoryGraph: list, match, mark-as-read with finish dates
- [x] Multi-source architecture (`--source`, pluggable provider registry, ISBN-first matching)
- [x] Goodreads source (CSV export → StoryGraph)
- [x] Kobo source (on-device `KoboReader.sqlite`)
- [x] LibraryThing source (CSV/JSON export) — _experimental_
- [x] Hardcover source (GraphQL) — _experimental_
- [x] Ratings & reviews sync, audio-edition tagging
- [ ] Harden experimental sources; verify cross-source de-duplication
- [ ] Audio-edition retag write side (today `retag` only reports)
- [ ] currently-reading sync, hosted UI
- [ ] Homebrew tap

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
