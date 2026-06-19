"""Literal source: reports finished books from a literal.club profile via GraphQL.

The queries and the response shapes consumed by the pure mappers were verified against
literal.club's published API (https://literal.club/pages/api) and two independent Python
clients (conatus/literal-tools, benbacardi/literal-export, 2026-06): ``login(email, password)``
returns ``{ token, profile { id } }``; ``booksByReadingStateAndProfile(readingStatus, profileId,
limit, offset)`` returns books carrying ``id``/``title``/``authors { name }``/``isbn10``/``isbn13``;
finish dates are NOT on the book object, so ``getReadDates(bookId, profileId)`` is queried per
finished book (an N+1 that ``finished_books`` budgets for); user ratings/reviews live on
``myReviews`` (paginated once and indexed by book id, not a second N+1). Status mapping uses the
documented ``ReadingStatus`` enum (``FINISHED``). All field access is defensive so a schema drift
degrades to empty/None rather than raising. EXPERIMENTAL pending a real run against a live token.

The bearer token is opaque (reportedly ~6-month lifetime, NOT confirmed to be a JWT — so no expiry
is parsed or assumed). Pass ``--token`` / set ``LITERAL_TOKEN``; obtain one programmatically with
``login_token(email, password)``, which runs the documented login mutation.

The pure mappers (``book_from_node``, ``finished_date_from_read_dates``, ``reviews_by_book_id``) are
unit-tested directly; ``LiteralSource`` wires them to an injectable transport so tests never touch
the network.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..models import SourceBook
from .base import SourceError

SOURCE_NAME = "literal"

API_URL = "https://literal.club/graphql/"
FINISHED_STATUS = "FINISHED"
PAGE_SIZE = 100

LOGIN_MUTATION = """
mutation StorywellLogin($email: String!, $password: String!) {
  login(email: $email, password: $password) {
    token
    profile { id }
  }
}
"""

PROFILE_QUERY = """
query StorywellProfile {
  me {
    id
  }
}
"""

FINISHED_BOOKS_QUERY = """
query StorywellFinishedBooks(
  $readingStatus: ReadingStatus!
  $profileId: String!
  $limit: Int!
  $offset: Int!
) {
  booksByReadingStateAndProfile(
    readingStatus: $readingStatus
    profileId: $profileId
    limit: $limit
    offset: $offset
  ) {
    id
    title
    isbn10
    isbn13
    authors { name }
  }
}
"""

READ_DATES_QUERY = """
query StorywellReadDates($bookId: String!, $profileId: String!) {
  getReadDates(bookId: $bookId, profileId: $profileId) {
    started
    finished
  }
}
"""

MY_REVIEWS_QUERY = """
query StorywellMyReviews($limit: Int!, $offset: Int!) {
  myReviews(limit: $limit, offset: $offset) {
    data {
      rating
      text
      book { id }
    }
  }
}
"""

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse_date(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_rating(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value or None


def _clean_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _parse_authors(book: dict[str, Any]) -> tuple[str, ...]:
    names = []
    for author in _as_list(book.get("authors")):
        name = _clean_text(_as_dict(author).get("name"))
        if name:
            names.append(name)
    return tuple(names)


def finished_date_from_read_dates(read_dates: Any) -> datetime | None:
    """Pick the latest ``finished`` timestamp from a ``getReadDates`` list, tolerant of gaps.

    A book can have several read-throughs; the most recent finish is the one that matters for a
    finished signal. Entries with no/unparseable ``finished`` (e.g. a started-but-not-finished
    re-read) are skipped rather than treated as finishing now."""
    finishes = [
        parsed
        for entry in _as_list(read_dates)
        if (parsed := _parse_date(_as_dict(entry).get("finished"))) is not None
    ]
    return max(finishes) if finishes else None


def reviews_by_book_id(review_data: Any) -> dict[str, tuple[float | None, str | None]]:
    """Index ``myReviews`` data into ``book id -> (rating, review)`` so a book's rating/review
    is one dict lookup instead of a per-book request. A later page wins on duplicate ids."""
    index: dict[str, tuple[float | None, str | None]] = {}
    for entry in _as_list(review_data):
        entry = _as_dict(entry)
        book_id = _as_dict(entry.get("book")).get("id")
        if book_id is None:
            continue
        index[str(book_id)] = (_parse_rating(entry.get("rating")), _clean_text(entry.get("text")))
    return index


def book_from_node(
    node: dict[str, Any],
    *,
    finished_at: datetime | None = None,
    rating: float | None = None,
    review: str | None = None,
) -> SourceBook:
    """Map one ``booksByReadingStateAndProfile`` book node to a finished ``SourceBook``.

    The node comes from a FINISHED query, so ``is_finished`` is always True; the finish date,
    rating, and review are supplied by the caller from the separate ``getReadDates`` / ``myReviews``
    lookups. Tolerant of missing keys at every level."""
    node = _as_dict(node)
    title = node.get("title")

    return SourceBook(
        source=SOURCE_NAME,
        source_id=str(node.get("id")),
        title=title.strip() if isinstance(title, str) else "",
        authors=_parse_authors(node),
        finished_at=finished_at,
        is_finished=True,
        rating=rating,
        review=review,
        isbn=_clean_text(node.get("isbn10")),
        isbn13=_clean_text(node.get("isbn13")),
    )


def _post(query: str, variables: dict[str, Any], token: str | None) -> dict[str, Any]:
    import httpx

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = httpx.post(
            API_URL, json={"query": query, "variables": variables}, headers=headers
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as err:
        raise SourceError(f"Network error talking to Literal: {err}") from err

    errors = _as_dict(payload).get("errors")
    if errors:
        raise SourceError(f"Literal GraphQL error: {errors}")
    return _as_dict(_as_dict(payload).get("data"))


def login_token(email: str, password: str) -> str:
    """Exchange Literal credentials for a bearer token via the documented login mutation.

    A convenience for obtaining the token to pass as ``--token`` / ``LITERAL_TOKEN``; the source
    itself never stores credentials, only the resulting opaque token."""
    if not (isinstance(email, str) and email.strip()):
        raise SourceError("Literal login needs an email.")
    if not (isinstance(password, str) and password):
        raise SourceError("Literal login needs a password.")
    data = _post(LOGIN_MUTATION, {"email": email, "password": password}, token=None)
    token = _clean_text(_as_dict(_as_dict(data).get("login")).get("token"))
    if not token:
        raise SourceError("Literal login did not return a token.")
    return token


def _default_transport(token: str) -> Transport:
    def transport(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        return _post(query, variables, token)

    return transport


class LiteralSource:
    """Reports finished books from a literal.club profile via the GraphQL API (EXPERIMENTAL)."""

    name = SOURCE_NAME

    def __init__(
        self,
        *,
        token: str | None = None,
        profile_id: str | None = None,
        transport: Transport | None = None,
    ):
        token = token or os.environ.get("LITERAL_TOKEN")
        if token is None and transport is None:
            raise SourceError(
                "Literal needs an API token. Pass --token or set LITERAL_TOKEN "
                "(obtain one with storywell.sources.literal.login_token(email, password))."
            )
        self.token = token
        self._profile_id = profile_id
        self._transport = transport

    @property
    def transport(self) -> Transport:
        if self._transport is None:
            self._transport = _default_transport(self.token or "")
        return self._transport

    def _resolve_profile_id(self) -> str:
        if self._profile_id:
            return self._profile_id
        data = self.transport(PROFILE_QUERY, {})
        profile_id = _clean_text(_as_dict(_as_dict(data).get("me")).get("id"))
        if not profile_id:
            raise SourceError("Literal did not return a profile id for this token.")
        self._profile_id = profile_id
        return profile_id

    def _paginate(self, query: str, base_variables: dict[str, Any], path: str) -> list[Any]:
        items: list[Any] = []
        offset = 0
        while True:
            data = self.transport(query, {**base_variables, "limit": PAGE_SIZE, "offset": offset})
            page = _as_list(self._extract(_as_dict(data), path))
            if not page:
                break
            items.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += len(page)
        return items

    @staticmethod
    def _extract(data: dict[str, Any], path: str) -> Any:
        value: Any = data
        for key in path.split("."):
            value = _as_dict(value).get(key)
        return value

    def _read_dates(self, book_id: str, profile_id: str) -> Any:
        data = self.transport(READ_DATES_QUERY, {"bookId": book_id, "profileId": profile_id})
        return _as_dict(data).get("getReadDates")

    def finished_books(self, *, threshold: float = 0.95) -> list[SourceBook]:
        """Literal marks completion with a FINISHED status, so ``threshold`` is unused here.

        One paginated FINISHED enumeration, one paginated ``myReviews`` for ratings/reviews, then
        one ``getReadDates`` per finished book (the documented N+1) for finish dates."""
        try:
            profile_id = self._resolve_profile_id()
            nodes = self._paginate(
                FINISHED_BOOKS_QUERY,
                {"readingStatus": FINISHED_STATUS, "profileId": profile_id},
                "booksByReadingStateAndProfile",
            )
            reviews = reviews_by_book_id(self._paginate(MY_REVIEWS_QUERY, {}, "myReviews.data"))
            books = []
            for node in nodes:
                book_id = str(_as_dict(node).get("id"))
                rating, review = reviews.get(book_id, (None, None))
                finished_at = finished_date_from_read_dates(self._read_dates(book_id, profile_id))
                books.append(
                    book_from_node(node, finished_at=finished_at, rating=rating, review=review)
                )
            return books
        except SourceError:
            raise
        except Exception as err:  # noqa: BLE001 - any transport failure becomes a SourceError
            raise SourceError(f"Literal request failed: {err}") from err
