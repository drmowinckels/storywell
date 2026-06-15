# PRD — v0.2: StoryGraph write-back

> **Status:** Draft for review (written autonomously overnight 2026-06-14/15).
> **Author:** Claude (Opus 4.8), for AM Mowinckel.
> **Scope:** Push the finished-audiobook list produced by v0.1 to The StoryGraph
> (mark as read + set finish date), using a Playwright-driven Chromium session.

This PRD is grounded in research done tonight against the live Audible library, the
old `good_audible_story_sync` source, and the StoryGraph site. The key findings that
shaped it are in the **Evidence** section at the bottom — read that first if a decision
looks surprising.

---

## 1. Pitch

A user who finishes audiobooks on Audible wants their StoryGraph "read" shelf to stay
current without re-entering each book by hand. v0.2 takes the finished list from v0.1
and, for each book, finds the matching StoryGraph edition and marks it read with the
correct finish date — driving a real Chromium session via Playwright so StoryGraph's
Cloudflare challenge (which killed the old Mechanize-based tool) never triggers. It's
worth doing now because v0.1 already produces a clean, dated finished list, the previous
community tool is dead, and the manual alternative is ~265 books of tedium.

## 2. Success criteria (testable)

- `make check` stays green; new StoryGraph modules carry unit tests with the browser
  layer mocked (target: ≥ 15 new tests, patch coverage ≥ 80% on changed files).
- `audible-storygraph-sync storygraph-login` opens a headed Chromium, lets the user log
  in by hand, and persists a session file; a second run reuses it **without** a login
  prompt (assert: storage-state file exists and is non-empty; second run reaches the
  profile page).
- `audible-storygraph-sync sync --dry-run` prints, for every finished book, one of:
  `MATCH` (StoryGraph book_id + confidence), `AMBIGUOUS`, or `NO MATCH` — and writes
  **nothing** to StoryGraph. Verifiable by log lines; exit 0.
- `audible-storygraph-sync sync` marks high-confidence matches as read with the v0.1
  finish date, skips already-read books (idempotent), and records what it did. Re-running
  immediately reports `0 newly marked` (assert via the run summary).
- A persisted ASIN → StoryGraph book_id mapping survives between runs so confirmed
  matches are never re-searched (assert: mapping file grows, search count drops on run 2).

## 3. Anti-goals (explicitly NOT in v0.2)

- **No Goodreads.** The old tool synced both; we do StoryGraph only.
- **No password handling by the app.** We never ask for, store, or type the user's
  StoryGraph password. Auth is a human login in a real browser; we persist only the
  resulting session cookies. (This is also the Cloudflare-robust path — see Evidence.)
- **No Cloudflare "bypass" engineering** (stealth plugins, fingerprint spoofing,
  CAPTCHA solvers). We sidestep it with a genuine headed session, not defeat it.
- **No reverse-sync** (StoryGraph → Audible), no currently-listening / progress sync,
  no ratings/reviews/star-pushing. Read-status + finish-date only.
- **No fully-unattended matching.** Ambiguous matches are reported, not guessed-and-written.
- **No Homebrew tap / packaging work** (that's v0.3).

## 4. Architecture (research-grounded)

### How StoryGraph actually works (no API)

StoryGraph has **no public API** (it's an open roadmap request with no ETA). Everything
is server-rendered HTML with forms. The old tool drove it with Mechanize until StoryGraph
added a Cloudflare bot challenge that headless/HTTP clients can't pass. Real browser
sessions started by a human are not challenged the same way — hence Playwright headed.

### Auth — persistent headed session (no credentials touched)

- First run (`storygraph-login`): launch Chromium with a **persistent context** /
  `headless=False`, navigate to `https://app.thestorygraph.com/users/sign_in`, and let
  the user log in manually (handles email/password, 2FA, "remember me", Cloudflare).
- On success (Profile link present, URL no longer `/users/sign_in`), save
  `context.storage_state(path=...)` to `~/.config/audible-storygraph-sync/storygraph-state.json`
  (mode 0600). This file holds the session cookies incl. `remember_user_token`.
- Subsequent runs load that storage state into a (preferably headless) context. If the
  session is expired/invalid, fail with a clear "run storygraph-login again" message.

### Matching — the hard part (no ISBN available)

The old tool matched by **ISBN**. Our live Audible data has **ISBN on 0% of items, ASIN
on 100%** — and StoryGraph does not index by Audible ASIN. So we must:

1. Search StoryGraph for `"<title> <first author>"` (StoryGraph's search box → results
   are links to `/books/{book_id}`).
2. Score candidates by normalized title + author similarity (strip subtitle/series
   noise; `subtitle` is present on ~⅔ of items and helps disambiguate). Prefer the
   audiobook edition when distinguishable.
3. Classify: **high-confidence** (auto-mark), **ambiguous** (report for manual
   confirmation), **no match** (report). Thresholds tunable; conservative by default.
4. Cache confirmed `ASIN → book_id` so a book is searched at most once, ever.

### Marking flow (replicate the old tool's steps in Playwright)

Verified from `good_audible_story_sync`'s Storygraph client:

1. Go to `/books/{book_id}`.
2. **Mark read:** submit the status form whose action matches
   `book_id={id}&status=read`.
3. **Set finish date:** open the "edit read date" control, then submit the
   `/read_instances/...` form, filling `read_instance[day]` / `[month]` / `[year]`
   from the v0.1 `finished_at`.
4. Treat "already has read status / finish date" as success and skip (idempotency).

### State & idempotency

A small JSON store in the config dir:

- `mappings`: `{asin: {book_id, confidence, confirmed_at}}`
- `synced`: `{asin: {finished_on, marked_at}}`
  Used to skip already-synced books and avoid re-searching. (SQLite is overkill for v0.2;
  revisit if the JSON gets unwieldy.)

## 5. Risks & open questions

**Risks**

- _DOM drift:_ StoryGraph HTML/selectors can change and silently break marking. Mitigate
  with narrow, well-named selectors and a `--dry-run` that exercises matching without writes.
- _Cloudflare on automated nav:_ even with a real session, headless reuse might get
  challenged. Mitigate: allow `--headed` fallback for the sync run, not just login.
- _Mismatch writes wrong book:_ worst case is marking the wrong edition read. Mitigate:
  conservative thresholds, `--dry-run` default-review, ambiguous→manual, never guess.
- _Rate/abuse:_ 265 sequential book ops could look abusive. Mitigate: polite delays,
  cap per-run, resumable via the synced-store.
- _ToS:_ automating a site with no API is a grey area. Mitigate: human login, human-paced,
  personal use, no bulk scraping of others' data. (Flag for AM's call.)

**Open questions (for AM in the morning)**

1. **Edition policy:** when StoryGraph has separate audiobook vs print editions, mark the
   _audiobook_ edition, or whichever is already on your shelf, or first match? _(Default
   I'll assume: prefer audiobook edition, else best title/author match.)_
2. **Confirmation UX:** for ambiguous matches, interactive prompt during `sync`, or
   batch them to a review file you resolve later? _(Default: report in `--dry-run`,
   interactive confirm in `sync`, `--yes` to auto-accept high-confidence only.)_
3. **The 0%-but-finished batch** (your 2023-08-18 bulk-mark, ~71 books): push these too,
   or only push books actually listened ≥ threshold? _(Default: push all v0.1-finished,
   since that's your real finished shelf.)_
4. **Where to store session/state:** `~/.config/audible-storygraph-sync/` ok? _(Default: yes.)_
5. **Playwright as a hard dep** vs optional `[storygraph]` extra (it's a heavy dep +
   `playwright install chromium`)? _(Default: optional extra, with a clear error if missing.)_

## 6. First slice (stacked PR #1) — auth + session, no writes

Smallest shippable end-to-end piece: prove we can establish and reuse a StoryGraph
session. No matching, no marking yet.

- **Add** `src/audible_storygraph_sync/storygraph/__init__.py`
- **Add** `src/audible_storygraph_sync/storygraph/session.py` — launch persistent
  Chromium, manual-login flow, save/load storage state, `is_authenticated()` check.
- **Add** `src/audible_storygraph_sync/config.py` — config-dir resolution + state paths
  (0600), so this and the audible side share one home.
- **Edit** `src/audible_storygraph_sync/cli.py` — add `storygraph-login` command.
- **Edit** `pyproject.toml` — add `playwright` (optional `[storygraph]` extra) + a
  "needs `playwright install chromium`" hint.
- **Add** `tests/test_storygraph_session.py` — mock the Playwright API; test
  state save/load, the authenticated/expired branches, and the "missing playwright"
  error path. (Browser launch itself is the untestable shim; everything around it is pure.)

**Proof it works:** `storygraph-login` creates a non-empty `storygraph-state.json`;
re-running `is_authenticated()` against a saved state returns true in a test with a
mocked context; `make check` green.

## 7. Slice plan (stacked PRs after #1)

2. **Search + matching (read-only)** — `storygraph/search.py`, `matching.py`,
   `sync --dry-run` printing MATCH/AMBIGUOUS/NO MATCH. Pure scoring logic is heavily
   unit-tested on captured HTML fixtures; no writes.
3. **Mark-as-read + finish-date** — `storygraph/client.py` marking flow, the persisted
   synced-store, idempotent `sync`. Writes gated behind explicit run (not `--dry-run`).
4. **Polish** — resumability, rate-limiting/delays, `--headed` fallback, run summary,
   docs. Then cut the v0.2 release and update the README roadmap.

---

## Evidence (collected 2026-06-14/15)

- **Audible identifiers (live library, 301 items):** ASIN 100%, **ISBN 0%**, subtitle
  ~67%, series metadata absent in current response groups. → ISBN matching (old tool's
  approach) is not viable; title/author search + scoring is required.
- **StoryGraph has no public API** — roadmap feature request, no ETA. Server-rendered
  forms only. Unofficial `ym496/storygraph-api` works off the `remember_user_token`
  cookie, confirming cookie-session auth.
- **Cloudflare:** vanilla headless Playwright is detected via `navigator.webdriver` / CDP
  fingerprints; "bypass" tooling is an arms race. A genuine headed human login avoids the
  challenge entirely — matches the README's stated plan.
- **Old tool's StoryGraph flow** (`cheshire137/good-audible-story-sync`, `lib/.../storygraph/`):
  - sign-in: GET `/users/sign_in`, fields `user[email]`/`user[password]`.
  - book page: `/books/{book_id}`.
  - mark read: form action contains `book_id={id}&status=read`.
  - finish date: open read-date editor → `/read_instances/` form, `read_instance[day|month|year]`.
  - matching was keyed by ISBN (which we don't have).

### Sources

- StoryGraph API roadmap (no API): https://roadmap.thestorygraph.com/features/posts/an-api
- Unofficial cookie-based API: https://github.com/ym496/storygraph-api
- Old tool (dead, Mechanize/Cloudflare): https://github.com/cheshire137/good-audible-story-sync
- Cloudflare + Playwright detection background: https://www.browserstack.com/guide/playwright-cloudflare
