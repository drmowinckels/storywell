# Storywell

Sync the books you've finished — across every vendor — into [The StoryGraph](https://app.thestorygraph.com).

Storywell reads your finished books from a _source_ and marks them as read on StoryGraph,
which has no public import API. [Audible](https://audible.com) is the first source;
the architecture is built so [Goodreads](https://www.goodreads.com) and other vendors slot in
behind the same `--source` flag.

> **Status:** v0.2 alpha. Audible → StoryGraph read sync works (list finished books, match,
> mark-as-read with finish dates, idempotent re-runs). Additional sources are on the roadmap.

## Why this exists

The previous community tool, [`good_audible_story_sync`](https://github.com/cheshire137/good-audible-story-sync),
no longer works: StoryGraph put up a Cloudflare bot challenge that its Mechanize-based scraper cannot solve.
Storywell replaces it with a Python CLI that uses the actively maintained
[`audible`](https://github.com/mkb79/Audible) library on the source side and a real Chromium
session via Playwright on the StoryGraph side, sidestepping Cloudflare entirely.

## Install

```sh
pipx install storywell
```

StoryGraph write-back needs Playwright's browser:

```sh
pipx inject storywell playwright
playwright install chromium
```

### Audible source

The Audible source needs [`audible-cli`](https://github.com/mkb79/audible-cli) to register your
account once:

```sh
pipx install audible-cli
audible quickstart
```

Storywell reads `~/.audible/config.toml` to find the active profile's auth file. If you use a
non-default profile, pass `--profile <name>`. To bypass discovery, pass `--auth-file /path/to/profile.json`.

## Usage

```sh
storywell sources                          # which vendors Storywell can read from
storywell list                             # list finished books (default source: audible)
storywell list --source audible -t 0.9     # lower the "finished" threshold
storywell storygraph-login                 # log in to StoryGraph once (saves a session)
storywell sync --dry-run                   # preview the StoryGraph match plan
storywell sync                             # mark finished books as read on StoryGraph
```

`--threshold` (default `0.95`) sets the listening-progress cutoff above which a book is treated as
finished, in addition to anything the source itself flags as complete.

`sync` writes high-confidence matches directly, prompts you on ambiguous ones, and is idempotent:
a book is only (re)written if its finish date changed. Matches are keyed by `source:id`, so multiple
vendors never collide.

## Upgrading from `audible-storygraph-sync`

The rename moved the config dir and namespaced sync-store keys under their source. Carry your saved
StoryGraph login and sync history forward once — non-destructively — with:

```sh
storywell migrate-store
```

## Roadmap

- [x] Audible → StoryGraph: list, match, mark-as-read with finish dates
- [x] Multi-source architecture (`--source`, pluggable provider registry)
- [ ] Goodreads source (CSV export → StoryGraph)
- [ ] Additional audiobook/ebook vendors
- [ ] Homebrew tap
- [ ] currently-reading sync, hosted UI

## Adding a source

A source is a class with a `name` and a `finished_books()` method that returns `SourceBook`s
(see [`src/storywell/sources/base.py`](src/storywell/sources/base.py)). Register it in
[`src/storywell/sources/__init__.py`](src/storywell/sources/__init__.py) and it becomes available
under `--source <name>`. The StoryGraph matching/write side is source-agnostic.

## Development

```sh
git clone https://github.com/drmowinckels/storywell.git
cd storywell
make install
make check        # lint + format check + tests
```

## License

MIT
