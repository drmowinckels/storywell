# PRD — v0.3: Collections (mark the contained books)

> **Status:** Draft for review (2026-06-15).
> **Scope:** When a finished Audible item is a multi-book _collection/omnibus_, mark the
> individual contained books as read on StoryGraph (via a confirmable checklist) instead
> of marking the omnibus itself.
> Builds on v0.2 (`sync`); see the investigation findings baked into §4.

## 1. Pitch

A reader who finishes a collection on Audible (e.g. "The Complete Jane Austen Collection")
wants each _contained_ novel marked read on StoryGraph — not a single omnibus entry that
leaves Pride and Prejudice, Emma, etc. looking unread. v0.3 detects collections, proposes
the books inside them, and lets the user confirm which to mark. It's worth doing because
~14 of the user's finished items are true collections, and marking the omnibus (current
v0.2 behaviour) under-counts their actual reading.

## 2. Success criteria (testable)

- `make check` stays green; the collection detector and description parser are pure and
  unit-tested (≥ 10 new tests), patch coverage ≥ 80% on changed files.
- `is_collection(item)` returns true for the ~14 collection-titled MultiPartBooks and
  false for ordinary MultiPartBooks (Jade City etc.) — asserted on fixtures.
- `sync` (or `sync-collections`), on a collection, prints a checklist of proposed
  contained titles and, on confirmation, marks each matched book read with the
  collection's finish date — verifiable by run summary (`collection X → N books marked`).
- Re-running is idempotent: already-marked contained books are skipped (store keys on the
  child identity, not the omnibus).
- A collection with no parseable description falls back to manual entry (empty checklist,
  user types titles) — exercised by a test.

## 3. Anti-goals

- **No reliance on Audible for the book list** — Audible's collection children are just
  `"<Title> Part N"` audio segments; it does not name the works (confirmed). The list
  comes from StoryGraph's description (parsed) or the user.
- **No perfect parser.** The description parse is best-effort; the user always confirms/edits.
- **No automatic un-marking** of omnibuses already synced as themselves — offer it as an
  explicit, separate confirm (don't silently delete reads).
- **No series-expansion** (treating every series volume as "contained") — series pages
  list whole books, not a collection's parts.
- **No new auth / no Goodreads / no schema migration** beyond the sync store.

## 4. Findings that shape the design (verified 2026-06-15)

- **Detection (Audible):** `content_delivery_type == "MultiPartBook"` is _not_ a signal
  (221/301 are MultiPartBook). Use title keywords: `collection | omnibus | complete novels
| definitive | anthology | quartet | the complete <author>`. Exclude `trilogy, Book N`
  (single volumes). ~14 true collections result.
- **Audible children:** `relationships` (type `component`/`child`) give child ASINs +
  sort order but `title: None`; fetching each child via `1.0/catalog/products/{asin}`
  returns only `"<Collection> Part N"` — useless for book names.
- **StoryGraph source:** omnibus pages do _not_ structurally list contained works; the
  works appear only in the free-text **Description** (e.g. Jane Austen: "Included are the
  following: Major Works: Sense and Sensibility (1811), Pride and Prejudice, …"). Series
  pages (`/series/{id}`) list volumes cleanly but at the wrong granularity for author
  collections.
- **Conclusion:** parse the StoryGraph omnibus Description → propose titles → user
  confirms → reuse the existing v0.2 search+match+mark pipeline per confirmed book.

## 5. Risks & open questions

**Risks**

- _Parser misses / over-captures_ across varied description formats → mitigate: always an
  editable checklist, never auto-commit parsed titles.
- _Wrong omnibus matched_ (the description we parse is from the wrong SG listing) → show
  which omnibus page was used; let the user reject.
- _Double-counting_ if both the omnibus and its books end up read → offer to un-mark the
  omnibus; key the store on child identity.
- _Detection false-positives_ (a single book with "Collection" in the title) → keyword
  list + the user confirming before any write.

**Open questions**

1. Surface as part of `sync` (auto-detect mid-run) or a separate `sync-collections` command?
   _(Lean: separate command — collections are interactive and few.)_
2. Finish date for contained books: the collection's `finished_at` for all, or leave
   undated? _(Lean: collection's finish date for all.)_
3. Auto-offer to un-mark the already-synced omnibuses (Dickens ×2, Wool)? _(Lean: yes, with a confirm.)_

## 6. First slice (stacked PR #1) — detection + parser, read-only

Smallest shippable piece: identify collections and propose their contents; no writes.

- **Add** `storygraph/collections.py` — `is_collection(item) -> bool` (title keywords +
  MultiPartBook), `parse_contained_titles(description) -> list[str]` (best-effort).
- **Add** `cli` command `collections --dry-run` — lists each detected collection, the SG
  omnibus it found, and the proposed contained titles. No writes.
- **Add** `tests/test_collections.py` — detector true/false fixtures; parser on the Jane
  Austen description fixture + an empty/garbage description.

**Proof:** `collections --dry-run` prints the ~14 collections with proposed titles;
`make check` green. Subsequent slices: interactive checklist + mark each; then the
un-mark-omnibus confirm.
