# PRD — Multi-source expansion (Goodreads, LibraryThing, Hardcover, Libro.fm, Kobo, Libby, Kindle)

> **Status:** Draft for review (2026-06-16).
> **Author:** Claude (Opus 4.8), for AM Mowinckel.
> **Scope:** Add new _sources_ behind the existing `--source` flag so Storywell can read
> finished books from book-shelf and audiobook/ebook services beyond Audible, and push them
> to The StoryGraph with the source-agnostic match/mark/rate pipeline already built in v0.2–v0.3.
> This is a **survey + strategy** PRD covering seven candidate vendors at a high level, plus one
> concrete buildable first slice (Goodreads). Build order is recommended in §8.

This is grounded in research done 2026-06-15/16 against each vendor's export/API surface; the
load-bearing facts and citations are in the **Evidence** section — read it first if a call looks
surprising. LibraryThing and Hardcover specifics are from general knowledge, **not** live-verified
this pass (see Open questions).

---

## 1. Pitch

A reader keeps their "read" history in more than one place — Goodreads for years of ratings and
reviews, an audiobook app for what they listened to, a library app for what they borrowed — and
wants all of it reflected on StoryGraph without re-typing. Storywell already turns "a finished
book from a source" into "marked read on StoryGraph (with finish date, rating, and review)"; this
expansion adds the other sources behind the same `--source` flag, reusing the entire StoryGraph
side untouched. It's worth doing now because the source abstraction is already proven with Audible,
most of these vendors expose **ISBNs** (which makes matching _easier_ than Audible ever was), and
the README roadmap already promises Goodreads + "additional audiobook/ebook vendors."

## 2. Success criteria (testable)

- `make check` stays green; every new source ships pure parser/mapper unit tests (≥ 10 per source)
  with the live fetch/file boundary mocked; patch coverage ≥ 80% on changed files.
- `storywell sources` lists each newly added vendor; `storywell list --source <vendor> [--file …]`
  prints its finished books in the existing table — asserted per source on a captured fixture.
- A new **ISBN-first match path** is exercised: given a `SourceBook` carrying an ISBN13, matching
  resolves a StoryGraph `book_id` without a title/author search when the ISBN hits — asserted on a
  fixture, with title/author fallback when it misses.
- `storywell sync --source goodreads --file goodreads_library_export.csv` marks the CSV's `read`
  shelf books read with their `Date Read` and writes rating + review, is **idempotent** on re-run
  (`0 newly marked`), and never double-writes a book already present from another source (store is
  keyed `source:source_id`).
- Each file-based source rejects a malformed/empty export with a clear error and non-zero exit —
  asserted by a test, not a stack trace.

## 3. Anti-goals (explicitly NOT in this expansion)

- **Not re-implementing StoryGraph's own Goodreads import.** StoryGraph already ingests a Goodreads
  CSV one-shot. Storywell's Goodreads path only justifies itself by being **incremental and
  duplicate-safe** (match against books already on the shelf, write only deltas). If we can't beat
  blind re-import on that axis, we don't ship it.
- **No credential handling for any vendor.** File-based sources take a user-exported file; API
  sources take a user-pasted token; scrape sources reuse the v0.2 human-login pattern. Storywell
  never stores or types a vendor password.
- **No fabricated "finished" status.** Where a vendor only proves _purchased_ (Libro.fm) or _opened_
  (Kindle) or _borrowed_ (Libby), we do not silently treat that as read — completion must be a real
  signal or an explicit user opt-in, never a guess.
- **No reverse-sync, no currently-reading/progress sync, no to-read/want-to-read shelf sync** in
  this phase. Finished → read (+ finish date, rating, review) only, matching v0.2 scope.
- **No live scraping of Goodreads/LibraryThing/Hardcover web UIs** — use their export/API. Scrape is
  reserved for vendors with no export at all (Libro.fm completion, Kindle live).
- **No phone/e-reader device drivers.** Kobo's on-device DB is read from a path the user points us
  at; we don't manage USB mounting or device sync.

## 4. Vendor survey (verified where cited)

Three integration patterns cover all seven. The whole StoryGraph side (search, match, mark-read,
ratings/reviews, collections) is reused as-is; only the **left edge** — turning a vendor's data into
`SourceBook`s — is new per vendor.

| Vendor                | Pattern                                | Identifiers      | Rating / Review          | Completion signal                                            | Feasibility                                     |
| --------------------- | -------------------------------------- | ---------------- | ------------------------ | ------------------------------------------------------------ | ----------------------------------------------- |
| **Goodreads**         | CSV drop                               | ISBN, ISBN13     | ✅ rating (1–5) + review | `Exclusive Shelf = read` + `Date Read`                       | **Medium** (easy mechanic; value = incremental) |
| **LibraryThing**      | CSV/TSV/JSON drop (or API)             | ISBN-rich        | ✅ rating + review       | "Read" collection + date                                     | **Easy–Medium** ⚠ unverified                    |
| **Hardcover**         | GraphQL API + token                    | ISBN/edition ids | ✅ rating + review       | read status + finished date                                  | **Medium** ⚠ unverified                         |
| **Libro.fm**          | CSV (metadata) + scrape (completion)   | none in export   | ❌                       | export = _purchase_ only; finished needs library-page scrape | **Hard** (purchase ≠ listened)                  |
| **Kobo**              | On-device SQLite (`KoboReader.sqlite`) | ISBN             | ❌                       | `ReadStatus = 2` + `DateLastRead` (clean!)                   | **Medium** (needs device file)                  |
| **Libby / OverDrive** | CSV (Timeline export)                  | none             | ❌                       | borrow/return _events_, not completion                       | **Hard** (borrowing ≠ finished)                 |
| **Kindle / Amazon**   | "Request My Data" export (or scrape)   | ASIN             | ❌                       | reading sessions / page-turns; **no finished flag**          | **Medium–Hard** (infer completion)              |

**Pattern A — file/CSV drop** (Goodreads, LibraryThing, Libro.fm metadata, Libby Timeline, Kindle
data-request): user exports a file; a generic `CsvSource` with a per-vendor **column profile** maps
it to `SourceBook`. New vendor in this class ≈ one profile + tests.

**Pattern B — API pull** (Hardcover): a token-authenticated GraphQL query returns read books
directly. Closest to a "real" sync; no file handoff.

**Pattern C — local DB / scrape** (Kobo on-device SQLite; Libro.fm & Kindle live completion): read a
SQLite file the user points at, or reuse the v0.2 Playwright human-login path for sites with no
export. Highest effort; reserve for vendors with no cleaner route.

## 5. Cross-cutting design changes (the shared work)

These are the only changes outside a per-vendor module — done once, every source benefits:

1. **`SourceBook.isbn` / `isbn13`** (new optional fields on [models.py](../src/storywell/models.py)).
   The model already carries `rating`, `review`, `finished_at`, `is_finished` — only stable book
   identifiers are missing. Audio sources leave them empty (as `narrators` already does for text).
2. **ISBN-first matching** in [matching.py](../src/storywell/storygraph/matching.py) +
   [search.py](../src/storywell/storygraph/search.py): when a `SourceBook` has an ISBN, resolve the
   StoryGraph edition by ISBN (StoryGraph search accepts ISBNs) and skip title/author scoring on a
   hit; fall back to the existing fuzzy path on a miss. This is the single biggest matching-quality
   win and is what makes the shelf services lower-risk than Audible.
3. **Generic `CsvSource`** in `sources/` + a `--file/--csv PATH` CLI option. `make_source` already
   drops options a source doesn't declare, so adding a `path` option to the shared CLI surface is
   non-breaking (Audible ignores it).
4. **Registry entries** in [sources/**init**.py](../src/storywell/sources/__init__.py) — each new
   vendor is one `SOURCES` line, exactly as documented in the README's "Adding a source."

No StoryGraph-side write code changes; collections (v0.3) and ratings/reviews stay as-is.

## 6. Risks & open questions

**Risks**

- _Goodreads redundancy:_ StoryGraph already imports the same CSV. If incremental/duplicate-safe
  sync isn't meaningfully better, the feature is noise → gate on the dedup behaviour (open question).
- _ISBN ≠ edition the user read:_ ISBN match can resolve a different edition than intended → prefer
  the work-level match StoryGraph returns; keep finish-date/rating attached to the work, not edition.
- _Excel-formula ISBNs:_ Goodreads writes `="9780…"`; LibraryThing/others quote oddly → robust
  un-wrapping in the CSV layer, unit-tested on the real malformed forms.
- _False "finished":_ Libro.fm export = purchases, Libby = borrows, Kindle = opens → never map these
  to read without a real completion signal or explicit user confirmation (anti-goal #3).
- _Kobo device coupling:_ reading `KoboReader.sqlite` needs the user to plug in / locate the file;
  schema can drift across firmware → pin to documented columns, fail loudly on absence.
- _Scope sprawl:_ seven vendors is a lot. Mitigate by shipping Pattern A generically first; B and C
  only for vendors AM actually uses.

**Open questions**

1. **StoryGraph re-import dedup:** does StoryGraph merge or duplicate on re-importing a Goodreads
   CSV? The positioning rests on this and is currently inferred from user bug reports, not docs —
   confirm with a small test import before locking Goodreads scope.
2. **LibraryThing export fields + API** — not live-verified this pass: exact CSV columns, rating
   scale, whether the "Read" collection/date is exported, and whether the public API is usable for a
   personal pull. Verify before building that source.
3. **Hardcover API** — not live-verified: confirm it's GraphQL, the token/auth flow, the per-user
   read-status/rating/review/date fields, and rate limits/ToS for personal use.
4. **Kindle completion heuristic:** what session/page-turn threshold counts as "finished," and is it
   worth it vs. just letting the user confirm? (Probably confirm-only first.)
5. **Which vendors does AM personally use?** Build B/C sources only for those; the rest stay surveyed.

## 7. First slice (stacked PR #1) — Goodreads via a generic CSV source + ISBN matching

Smallest shippable, highest-value end-to-end piece: prove the file-drop pattern and ISBN-first
matching on the vendor everyone has. No API, no scrape, no device.

- **Edit** [`src/storywell/models.py`](../src/storywell/models.py) — add optional `isbn` / `isbn13`.
- **Add** `src/storywell/sources/csv_source.py` — `CsvSource(path, profile)`: read a CSV, apply a
  column-mapping **profile**, yield `SourceBook`s; un-wrap `="…"` ISBNs; tolerate blank fields.
- **Add** `src/storywell/sources/goodreads.py` — the Goodreads profile (column map; `Exclusive
Shelf == read` ⇒ finished; `My Rating` 1–5, `0` ⇒ unrated; `Date Read` ⇒ `finished_at`;
  `My Review` ⇒ review) + `GoodreadsSource` wrapping `CsvSource`.
- **Edit** [`src/storywell/sources/__init__.py`](../src/storywell/sources/__init__.py) — register
  `goodreads`.
- **Edit** [`src/storywell/storygraph/matching.py`](../src/storywell/storygraph/matching.py) +
  [`search.py`](../src/storywell/storygraph/search.py) — ISBN-first resolve, title/author fallback.
- **Edit** [`src/storywell/cli.py`](../src/storywell/cli.py) — add a `--file/--csv PATH` option
  (carried on the shared surface; ignored by Audible).
- **Add** `tests/test_goodreads.py`, `tests/test_csv_source.py`, and ISBN-match tests using a small
  captured `goodreads_library_export.csv` fixture (read shelf, currently-reading, unrated, malformed
  ISBN, review-with-newlines).

**Proof it works:** `storywell list --source goodreads --file <fixture>.csv` prints the read-shelf
books; `storywell sync --source goodreads --file <fixture>.csv --dry-run` shows ISBN-resolved
MATCHes; a real run marks them read with dates + ratings + reviews and reports `0 newly marked` on
the second run; `make check` green.

## 8. Recommended build order

1. **Goodreads (CSV) + ISBN matching** — §7. Unlocks the generic CSV pattern and the shared ISBN win.
2. **LibraryThing (CSV)** — once §7 lands, this is mostly a second column profile (after verifying
   open question 2). Cheapest follow-on; rating/review carry over.
3. **Hardcover (GraphQL API)** — first Pattern-B source; the cleanest _live_ sync (after open
   question 3). Good template for any future API vendor.
4. **Kobo (on-device SQLite)** — first Pattern-C source with a _clean_ finished flag; no scraping.
5. **Kindle / Libby / Libro.fm** — only if AM uses them; each needs completion inference or a scrape
   and gives no ratings/reviews, so lowest value-per-effort. Survey-documented; defer by default.

---

## Evidence (collected 2026-06-15/16)

**Goodreads** — Public API **retired**: Goodreads stopped issuing new keys 2020-12-08 and disabled
existing keys; no public API since. Only route out is the manual **CSV export** (My Books → Import
and Export → Export Library → `goodreads_library_export.csv`). Verified 24 columns, in order:
`Book Id, Title, Author, Author l-f, Additional Authors, ISBN, ISBN13, My Rating, Average Rating,
Publisher, Binding, Number of Pages, Year Published, Original Publication Year, Date Read, Date
Added, Bookshelves, Bookshelves with positions, Exclusive Shelf, My Review, Spoiler, Private Notes,
Read Count, Owned Copies`. `My Rating` is 1–5 integer (`0` = unrated); ISBN/ISBN13 stored as Excel
formula text `="…"` (must strip); DNF is not a native exclusive shelf. **StoryGraph natively imports
this CSV** (async, carries shelves→read/currently-reading/to-read/DNF, custom shelves→tags, ratings,
reviews, dates, spoiler flags) — but it is **one-shot, not incremental**, and re-import is a
documented source of **duplicates** with no dedupe/merge. That gap is Storywell's reason to exist
here. Export is manual (no automation hook), generated in seconds–minutes.

**Libro.fm** — Logged-in CSV export at `libro.fm/user/library/export.csv`; columns: Audiobook name,
Author(s), Narrator(s), Publication date, Purchase date. **No ISBN, no rating/review, no progress or
finished status** — proves purchase, not listening. Completion requires scraping the logged-in
library page (Audible/Playwright-style). No API.

**Kobo** — No official export/API, but on-device `KoboReader.sqlite` (`.kobo/` folder) `content`
table (`ContentType=6`) exposes Title, Subtitle, Attribution (author), Series, ISBN, `ReadStatus`
(0 unopened / 1 reading / 2 read), `___PercentRead`, `DateLastRead`, Publisher. Clean finished flag

- date; no rating/review. Needs physical device access.

**Libby / OverDrive** — Official **Timeline export** (Shelf → Timeline → Actions → Export Timeline →
CSV `libbytimeline-activities`); OverDrive site also emails a CSV history. Records borrow/return/hold
**events**, not completion; no ISBN/rating. Borrowing ≠ ownership ≠ finished.

**Kindle / Amazon** — No live API. Official **"Request My Data" → Kindle** export (days to arrive):
`Kindle.KindleDocs.DocumentMetadata.csv` (metadata + ASIN, first-opened, session counts) and
`Kindle.Devices.ReadingSession.csv` (timestamped sessions). **No explicit finished flag; no
rating/review** — completion must be derived. Faster alt: scrape `read.amazon.com` /
`amazon.com/yourbooks` (title/author/ASIN).

**LibraryThing & Hardcover** — ⚠ **not live-verified this pass.** Working assumptions: LibraryThing
offers CSV/TSV/JSON export and a limited public API, ISBN-rich, with ratings/reviews and a "Read"
collection; Hardcover (hardcover.app) offers an official **GraphQL API** with user tokens covering
read status, rating, review, dates, and edition identifiers. Confirm both before building (open
questions 2–3).

### Sources

- Goodreads API retired: https://www.infodocket.com/2020/12/13/report-goodreads-plans-to-retire-api-access-disables-existing-api-keys/ · https://pxlnv.com/linklog/goodreads-discontinues-api/
- Goodreads export columns (verbatim): https://www.goodreads.com/topic/show/22566592-what-s-in-an-export-file · https://minilib.app/blog/how-to-export-goodreads-library-complete-guide-2025/
- StoryGraph Goodreads import + duplicate behaviour: https://houselucia.com/import-goodreads-to-storygraph/ · https://roadmap.thestorygraph.com/bugs/posts/multi-entries-for-books-with-multiple-tags
- Libro.fm export: https://support.libro.fm/support/solutions/articles/48001267215-can-i-export-a-list-of-my-libro-fm-audiobook-purchases-
- Kobo SQLite: https://www.reallinuxuser.com/how-to-extract-kobo-book-and-reading-data-from-the-koboreader-sqlite-file-with-beekeeper-studio/ · https://github.com/eliascotto/export-kobo
- Libby/OverDrive Timeline export: https://help.libbyapp.com/en-us/6207.htm · https://help.overdrive.com/en-us/1137.htm
- Kindle "Request My Data": https://jakelee.co.uk/analysing-5-years-of-amazon-kindle-reading/
