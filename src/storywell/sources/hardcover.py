"""Hardcover source: reads the reader's shelf and reports finished books via GraphQL.

The query and the response shape consumed by ``book_from_node`` were verified against
Hardcover's official GraphQL SDL (hardcoverapp/hardcover-docs ``schema.graphql``, 2026-06):
``me`` is a list; ``user_books`` carries ``book``/``status_id``/``rating``/``review_raw``/
``last_read_date``; ``books`` exposes ``editions`` and ``contributions``; ``editions`` carry
``isbn_13``/``isbn_10``; ``contributions.author.name`` is the author. Not yet exercised against
a live token, and ``status_id == 3`` ("Read") is Hardcover's documented status mapping rather
than something the SDL pins down — so still EXPERIMENTAL pending a real run. All field access
is defensive so a schema drift degrades to empty/None rather than raising. Get a token at
https://hardcover.app/account/api (or set ``HARDCOVER_TOKEN``).

``book_from_node`` is a pure mapping function and is unit-tested directly; ``HardcoverSource``
wires it to an injectable transport so tests never touch the network.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..models import SourceBook
from .base import SourceError

SOURCE_NAME = "hardcover"

API_URL = "https://api.hardcover.app/v1/graphql"
STATUS_READ = 3

READ_BOOKS_QUERY = """
query StorywellFinishedBooks {
  me {
    user_books {
      status_id
      rating
      review_raw
      last_read_date
      book {
        id
        title
        contributions { author { name } }
        editions { isbn_13 isbn_10 }
      }
    }
  }
}
"""

Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _parse_last_read_date(raw: Any) -> datetime | None:
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


def _parse_authors(book: dict[str, Any]) -> tuple[str, ...]:
    names = []
    for contribution in _as_list(book.get("contributions")):
        name = _as_dict(_as_dict(contribution).get("author")).get("name")
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return tuple(names)


def _clean_isbn(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _parse_isbns(book: dict[str, Any]) -> tuple[str | None, str | None]:
    editions = _as_list(book.get("editions"))
    first = _as_dict(editions[0]) if editions else {}
    isbn13 = _clean_isbn(first.get("isbn_13"))
    isbn = _clean_isbn(first.get("isbn_10"))
    return isbn13, isbn


def book_from_node(node: dict[str, Any]) -> SourceBook:
    """Map one ``user_book`` node to a ``SourceBook``, tolerant of missing keys at every level."""
    node = _as_dict(node)
    book = _as_dict(node.get("book"))

    review_raw = node.get("review_raw")
    review = review_raw.strip() if isinstance(review_raw, str) and review_raw.strip() else None

    title = book.get("title")
    isbn13, isbn = _parse_isbns(book)

    return SourceBook(
        source=SOURCE_NAME,
        source_id=str(book.get("id")),
        title=title.strip() if isinstance(title, str) else "",
        authors=_parse_authors(book),
        finished_at=_parse_last_read_date(node.get("last_read_date")),
        is_finished=node.get("status_id") == STATUS_READ,
        rating=_parse_rating(node.get("rating")),
        review=review,
        isbn=isbn,
        isbn13=isbn13,
    )


def _default_transport(token: str) -> Transport:
    def transport(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        import httpx

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            response = httpx.post(
                API_URL,
                json={"query": query, "variables": variables},
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as err:
            raise SourceError(f"Network error talking to Hardcover: {err}") from err

        errors = _as_dict(payload).get("errors")
        if errors:
            raise SourceError(f"Hardcover GraphQL error: {errors}")
        return _as_dict(_as_dict(payload).get("data"))

    return transport


class HardcoverSource:
    """Reports finished books from a Hardcover shelf via the GraphQL API (EXPERIMENTAL)."""

    name = SOURCE_NAME

    def __init__(self, *, token: str | None = None, transport: Transport | None = None):
        token = token or os.environ.get("HARDCOVER_TOKEN")
        if token is None and transport is None:
            raise SourceError(
                "Hardcover needs an API token. Pass --token or set HARDCOVER_TOKEN "
                "(from https://hardcover.app/account/api)."
            )
        self.token = token
        self._transport = transport

    @property
    def transport(self) -> Transport:
        if self._transport is None:
            self._transport = _default_transport(self.token or "")
        return self._transport

    def finished_books(self, *, threshold: float = 0.95) -> list[SourceBook]:
        """Hardcover marks completion with a Read status, so ``threshold`` is unused here."""
        try:
            data = self.transport(READ_BOOKS_QUERY, {})
        except SourceError:
            raise
        except Exception as err:  # noqa: BLE001 - any transport failure becomes a SourceError
            raise SourceError(f"Hardcover request failed: {err}") from err

        me = _as_list(_as_dict(data).get("me"))
        user_books = _as_list(_as_dict(me[0]).get("user_books")) if me else []
        return [book for node in user_books if (book := book_from_node(node)).is_finished]
