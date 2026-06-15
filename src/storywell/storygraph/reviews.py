"""Pure helpers for writing a rating + review to StoryGraph.

StoryGraph stores a rating as ``stars_integer`` (0-5) + ``stars_decimal`` (quarter
stars: "", "25", "5", "75") and a review as ``review[explanation]``. These helpers
convert a source rating and compose the review text (with a narrator note, since
StoryGraph has no narrator field and some reviews are narration-specific). The browser
writer that submits them is built separately and needs live verification.
"""

from __future__ import annotations

_DECIMAL = {0: "", 25: "25", 50: "5", 75: "75"}


def rating_to_stars(rating: float) -> tuple[str, str]:
    """Split a 0-5 rating into StoryGraph's (stars_integer, stars_decimal) form."""
    integer = int(rating)
    fraction = round((rating - integer) * 100)
    return str(integer), _DECIMAL.get(fraction, "")


def compose_review(review: str | None, narrators: tuple[str, ...] = ()) -> str | None:
    """Combine a review body with a 'Narrated by …' note (StoryGraph has no narrator
    field). Returns None when there is nothing to write."""
    parts: list[str] = []
    if review and review.strip():
        parts.append(review.strip())
    if narrators:
        parts.append(f"Narrated by {', '.join(narrators)}.")
    return "\n\n".join(parts) if parts else None
