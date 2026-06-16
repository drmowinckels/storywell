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
    """Split a rating into StoryGraph's (stars_integer, stars_decimal) form.

    Clamped to 0-5 and snapped to the nearest quarter star, so an off-grid source
    rating (e.g. 3.7) maps to a value StoryGraph's selects actually offer (3.75)
    instead of silently dropping the fraction or failing select_option.
    """
    rating = max(0.0, min(5.0, rating))
    quarters = round(rating * 4) / 4
    integer = int(quarters)
    fraction = round((quarters - integer) * 100)
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
