# audible-storygraph-sync

Sync finished audiobooks from [Audible](https://audible.com) to [The StoryGraph](https://app.thestorygraph.com).

> **Status:** v0.1 alpha. This release only reads your Audible library and lists the books it considers
> _finished_. StoryGraph write-back lands in v0.2.

## Why this exists

The previous community tool, [`good_audible_story_sync`](https://github.com/cheshire137/good-audible-story-sync),
no longer works: StoryGraph put up a Cloudflare bot challenge that its Mechanize-based scraper cannot solve.
This project replaces it with a Python CLI that uses the actively maintained
[`audible`](https://github.com/mkb79/Audible) library on the Audible side and (in v0.2) a real Chromium
session via Playwright on the StoryGraph side, sidestepping Cloudflare entirely.

## Install

```sh
pipx install audible-storygraph-sync
```

You also need [`audible-cli`](https://github.com/mkb79/audible-cli) to register your Audible account once:

```sh
pipx install audible-cli
audible quickstart
```

`audible-storygraph-sync` reads `~/.audible/config.toml` to find the active profile's auth file. If you use a non-default profile, pass `--profile <name>`. To bypass discovery, pass `--auth-file /path/to/profile.json`.

## Usage

```sh
audible-storygraph-sync audible-list
audible-storygraph-sync audible-list --threshold 0.9
audible-storygraph-sync audible-list --auth-file ~/.audible/my-profile.json
```

`--threshold` (default `0.95`) sets the listening-progress cutoff above which a book is treated as
finished, in addition to anything Audible itself flags as complete.

## Roadmap

- [x] v0.1 — list finished Audible audiobooks
- [ ] v0.2 — Playwright-backed StoryGraph login + "mark as read" push
- [ ] v0.3 — Homebrew tap
- [ ] later — currently-listening sync, hosted UI

## Development

```sh
git clone https://github.com/drmowinckels/audible-storygraph-sync.git
cd audible-storygraph-sync
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## License

MIT
