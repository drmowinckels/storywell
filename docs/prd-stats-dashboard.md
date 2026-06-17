# PRD — StoryGraph stats dashboard

> **Status:** Draft for review (written autonomously 2026-06-17).
> **Author:** Claude (Opus 4.8), for AM Mowinckel.
> **Scope:** A local, offline dashboard that reads a user's StoryGraph **library CSV
> export** and turns it into rich, explorable, downloadable/shareable reading stats —
> the kind StoryGraph gates behind Plus or only shows as thin monthly overviews.
> **Branch:** `feat/storygraph-stats` (worktree `.claude/worktrees/stats`).

This reverses Storywell's data flow. Until now Storywell only **writes into** StoryGraph
(mark-as-read, dates, ratings). This feature **reads from** it — the user's own export —
and gives it back to them as stats they own. It stays on-brand: local-first, no server,
no credentials, the data never leaves the machine. The Evidence section at the bottom
records the confirmed export schema and the free-vs-Plus gap that motivate the design —
read it first if a decision looks surprising.

---

## 1. Pitch

A StoryGraph user wants to _understand and share_ their reading year — pace, moods,
formats, ratings, most-read authors — but the free tier shows only thin monthly overviews
and reserves the good charts for Plus. This feature reads the full library CSV that
StoryGraph lets **every** user export and renders a single self-contained HTML dashboard
they can explore, filter, and hand to a friend (or post). It's worth doing now because the
export is already rich (23 columns incl. moods, pace, ratings, formats, read dates),
Storywell already has battle-tested CSV-reading machinery, and "your reading data, yours
to look at" is exactly this project's thesis.

## 2. Success criteria (testable)

- `make check` stays green; new modules carry unit tests with no browser/network
  (target: ≥ 15 new tests, patch coverage ≥ 80% on changed files).
- `storywell stats --file export.csv` parses the export and prints a Rich summary:
  books read this/last year, average rating, format split, top 3 moods, longest/shortest
  read. Exit 0 on the bundled fixture (`tests/fixtures/storygraph_export_sample.csv`).
- The same command writes a **single self-contained** `storywell-stats.html` (no external
  CSS/JS/font requests — assert: the file opens offline and contains no `http(s)://` asset
  `src`/`href`). Opening it shows charts for: books-per-year, rating distribution, format
  breakdown, mood frequency, pace split, and a read-dates calendar/heatmap.
- The dashboard filters client-side by year and format **without a server** (assert: a
  Playwright DOM test toggles a year filter and the books-read count changes).
- A "Download PDF" button exports the whole dashboard via the browser's print-to-PDF using
  a `@media print` stylesheet (assert: the button calls `window.print()`; a print-CSS rule
  exists; numbers match the Rich summary).
- Parsing is robust to the real export's quirks: multi-value `Moods`, `Dates Read` ranges
  with re-reads (`a-b; c-d`), DNF/to-read rows, empty ratings, UTF-8 BOM (unit-tested).

## 3. Anti-goals (explicitly NOT in this PRD)

- **No scraping StoryGraph.** Input is the user's downloaded CSV only — no Playwright, no
  login, no live fetch. (Slice-0 simplicity and zero ToS risk.)
- **No hosted/multi-user service, no accounts, no upload.** It's a local file in, a local
  file out. "Share" means _the user_ sends the artifact, not us hosting it.
- **No page-count / words-read / genre stats.** The export omits page counts, genres, and
  publication year (see Evidence) — we will not invent or scrape them in v1.
- **No write-back and no cross-source merge.** This consumes a StoryGraph export; it does
  not touch the sync engine, `SourceBook`, or other vendors.
- **No recommendations / ML / "your reading personality" gimmicks.** Descriptive stats the
  user can verify against their own shelf, nothing inferred.
- **No mobile app / no Plus feature parity chase.** A good desktop-browser artifact, not a
  StoryGraph clone.

## 4. Architecture (data-grounded)

### Data source — the library export (all tiers)

StoryGraph's _visualisations_ are gated, but **CSV export is available to every user**
(Account → Manage Account → Export StoryGraph Library → emailed/downloaded CSV). Confirmed
header (23 columns), verbatim:

```
Title, Authors, Contributors, ISBN/UID, Format, Read Status, Date Added,
Last Date Read, Dates Read, Read Count, Moods, Pace, Character- or Plot-Driven?,
Strong Character Development?, Loveable Characters?, Diverse Characters?,
Flawed Characters?, Star Rating, Review, Content Warnings,
Content Warning Description, Tags, Owned?
```

What each unlocks:

| Field(s)                                    | Stat                                                                                                           |
| ------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `Read Status` + `Dates Read` + `Read Count` | books finished per year/month, re-reads, days-to-finish (from each `start-end` range), reading heatmap/streaks |
| `Star Rating`                               | rating distribution, mean, ratings over time, highest/lowest                                                   |
| `Format`                                    | audiobook/ebook/physical split, format over time                                                               |
| `Authors`, `Contributors`                   | most-read authors; narrators parsed from `Contributors`                                                        |
| `Moods` (multi-value)                       | mood frequency, mood-by-season                                                                                 |
| `Pace`                                      | fast/medium/slow split                                                                                         |
| character/plot booleans                     | a small "reading-taste fingerprint"                                                                            |
| `Tags` (user only), `Owned?`                | tag cloud; owned-vs-read overlap                                                                               |
| `Content Warnings`                          | frequency — **opt-in/collapsed by default** (sensitive)                                                        |

**Known gaps (v1 limitations, surfaced in the UI):** no page counts, no genres, no
publication year (all are open StoryGraph export requests). `Tags` contains only
user-added tags, not StoryGraph's auto genres. So "pages read" and "genre breakdown" are
out until an enrichment path exists.

### Model & modules

`SourceBook` is sync-shaped and lossy (no moods/pace/CW); stats needs its own record. New,
self-contained subtree so it never entangles the sync engine:

- `stats/export.py` — parse the export into a `LibraryEntry` dataclass (reuses
  `sources/csv_source.read_rows` for delimiter/encoding/BOM handling). Pure.
- `stats/parse.py` — the fiddly value parsers: `Dates Read` ranges + re-reads → list of
  `(start, end)`; `Moods` → list; ratings → float|None; `Read Status` enum. Pure, the
  test-heavy core.
- `stats/compute.py` — pure functions: each returns a small serialisable dict/list (one
  per chart). No rendering here, so every number is unit-testable in isolation.
- `stats/render.py` — Jinja2 template → one self-contained HTML (data embedded as JSON,
  charts + filters as vendored client-side JS). The only I/O-ish layer; thin.
- `cli.py` — add `stats` command (`--file/-f`, `--out`, `--open/--no-open`,
  `--year`, `--source storygraph` default).

### Delivery — self-contained HTML (recommended)

One generated `.html` file satisfies all three verbs at once: **launch & explore**
(open in any browser, client-side year/format filters), **download** (it _is_ the file;
plus a one-click share-card PNG), **share** (hand off a single file — no server, data never
leaves the machine). Charts via a small **vendored** JS lib (uPlot/Chart.js, inlined) so
the output has zero network dependencies — consistent with the project's offline,
privacy-first stance.

_Alternatives considered & deferred:_ **Quarto** parameterised dashboard (the docs site
already uses Quarto, but it's a heavy end-user dependency and weaker at "single shareable
file"); **Streamlit/Shiny** (great for explore, but needs a running server — fails
"download/share as an artifact"). Both can return later as an optional `--serve` mode.

## 5. Risks & open questions

**Risks**

- _Export format drift:_ StoryGraph could rename/re-order columns. Mitigate: map by header
  name (not position), tolerate missing optional columns, one golden fixture + a clear
  "unrecognised export" error.
- _Self-contained HTML bloat:_ inlining a chart lib + a large library could make a multi-MB
  file. Mitigate: pick a small lib (uPlot ~50 KB), embed data as compact JSON, lazy-render.
- _Date messiness:_ `Dates Read` mixes single dates, ranges, and `;`-joined re-reads;
  some `read` rows have no date at all. Mitigate: parser returns partial info, never throws;
  "undated reads" surfaced as an explicit bucket.
- _Sensitive content:_ content warnings and reviews are personal. Mitigate: CW collapsed by
  default; reviews excluded from the share card; nothing auto-uploaded.

**Resolved decisions (AM, 2026-06-17)**

1. **Delivery:** self-contained interactive HTML is the v1 target. A `--serve` app is
   explicitly out for now (can return later).
2. **Stats in scope:** all four groups front-and-center — **Volume & pace**, **Ratings**,
   **Formats & authors**, **Moods & taste**. Slice 1 computes all four so they're tested
   and ready for the HTML in slice 2.
3. **Share output:** **full-dashboard PDF** (not a single share-card PNG). Zero-dependency
   path is a "Download PDF" button driving the browser's print-to-PDF with a `@media print`
   stylesheet — no headless renderer. Lands in slice 3.
4. **Landing view:** current year, switchable to all-time (client-side).
5. **Packaging:** rendering lives behind an optional **`[stats]`** extra (like
   `[storygraph]`). Slice 1 is pure stdlib `csv` + Rich, so it needs **no** extra; the
   extra is introduced in slice 2 when Jinja2/templating arrives.

**Still open**

- Genres/page-counts are absent from the export (see Evidence). Revisit an optional
  enrichment path (e.g. ISBN → OpenLibrary) only after v1 ships — out of scope here.

## 6. First slice (stacked PR #1) — parse + compute + text summary, no HTML

Smallest shippable end-to-end piece: prove we can turn a real export into correct numbers.
No browser, no rendering — pure and fully testable.

- **Add** `src/storywell/stats/__init__.py`
- **Add** `src/storywell/stats/export.py` — `LibraryEntry` + `load_export(path)`.
- **Add** `src/storywell/stats/parse.py` — `Dates Read`, `Moods`, rating, status parsers.
- **Add** `src/storywell/stats/compute.py` — `books_per_year`, `rating_distribution`,
  `format_split`, `mood_frequency`, `pace_split`, `top_authors`, `summary`.
- **Edit** `src/storywell/cli.py` — `stats` command printing a Rich summary + `--json` dump.
- **Add** `tests/fixtures/storygraph_export_sample.csv` _(already added in this worktree)_.
- **Add** `tests/test_stats_parse.py`, `tests/test_stats_compute.py` — ranges/re-reads,
  multi-value moods, empty ratings, DNF/to-read exclusion, BOM; assert exact counts on the
  fixture (e.g. 6 read, 1 DNF, 1 to-read; mean rating; Project Hail Mary read twice).

**Proof it works:** `storywell stats -f tests/fixtures/storygraph_export_sample.csv` prints
the right counts and `--json` emits a stats blob whose numbers the unit tests pin;
`make check` green.

## 7. Slice plan (stacked PRs after #1)

2. **Static HTML dashboard** — `stats/render.py` (Jinja2 + embedded JSON + vendored charts),
   `--out`/`--open`, self-contained single file. Snapshot-test that key numbers appear in the
   rendered HTML; assert zero external asset URLs.
3. **Interactivity + share** — client-side year/format filters, a "Download PDF" button
   (`window.print()` + `@media print` stylesheet) exporting the full dashboard. One
   Playwright DOM test for the filter + the print path. Docs page + README roadmap update;
   cut the release.

---

## Evidence (collected 2026-06-17)

- **Export schema (confirmed, 23 columns):** `Title, Authors, Contributors, ISBN/UID,
Format, Read Status, Date Added, Last Date Read, Dates Read, Read Count, Moods, Pace,
Character- or Plot-Driven?, Strong Character Development?, Loveable Characters?, Diverse
Characters?, Flawed Characters?, Star Rating, Review, Content Warnings, Content Warning
Description, Tags, Owned?` — verbatim from a StoryGraph-CSV parser project. → Moods and
  Pace **are** in the export (an older source claimed otherwise; it's stale).
- **Gaps:** no page count, no genres, no publication year — all are open StoryGraph export
  feature requests. `Tags` is user-added tags only, not auto genres. → no pages/genre stats
  in v1 without enrichment.
- **Export is free for all tiers:** Account → Manage Account → Export StoryGraph Library.
  The gating is on _visualisations_, not the data. → a local renderer legitimately unlocks
  Plus-grade stats from data the user already owns.
- **Free vs Plus:** free = track, mood recs, reading goal, basic/monthly stats; Plus
  (~$4.99/mo) = Compare Stats, exclusive year-on-year charts, more detailed charts. → our
  value is richer, downloadable, _offline_ stats from the user's own export.
- **Reuse:** `sources/csv_source.read_rows` already handles delimiter detection +
  UTF-16/UTF-8-BOM/Latin-1 decoding — the stats parser builds on it rather than re-solving
  encoding.

### Sources

- StoryGraph CSV export columns (parser project): https://github.com/mateusz-bak/openreads/issues/525
- "Add genres to TSG library export" (genres absent from export): https://roadmap.thestorygraph.com/requests-ideas/posts/add-genres-to-tsg-library-export
- "Export your stats into CSV/Excel" (export scope discussion): https://roadmap.thestorygraph.com/requests-ideas/posts/export-your-stats-into-csv-excel
- StoryGraph Plus (free vs paid stats): https://app.thestorygraph.com/plus
- The StoryGraph features/pricing overview (2026): https://bookwiseapp.com/blog/the-storygraph-app-everything-you-need-to-know-in-2026
