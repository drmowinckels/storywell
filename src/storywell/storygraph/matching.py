from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

HIGH_CONFIDENCE = 0.85
MIN_PLAUSIBLE = 0.60
AMBIGUOUS_MARGIN = 0.08

_TITLE_NOISE = re.compile(
    r"\b(unabridged|a novel|dramatized adaptation|graphic audio|audio ?book"
    r"|omnibus|edition|collection)\b",
    re.IGNORECASE,
)
_TRANSLATOR = re.compile(r"\s*-\s*translator\b", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")

_FREE_PREFIX = re.compile(r"^\s*free(\s+story)?\s*:\s*", re.IGNORECASE)
_PAREN = re.compile(r"\s*\([^)]*\)")
_SERIES_SUBTITLE = re.compile(
    r":\s*.*\b(trilogy|series|saga|collection|book\s+\d+|volume\s+\d+)\b.*$",
    re.IGNORECASE,
)
_SERIES_TAIL = re.compile(r",\s*(book|volume|season|part)\b.*$", re.IGNORECASE)
_EDITION_WORDS = re.compile(r"\b(omnibus\s+edition|omnibus|definitive collection)\b", re.IGNORECASE)


def search_title(title: str) -> str:
    """A search-friendly title: drop FREE/series/omnibus noise that StoryGraph search
    doesn't index, so books like 'X: Some Trilogy, Book 2' or 'FREE STORY: Y' are found."""
    text = _FREE_PREFIX.sub("", title)
    text = _PAREN.sub("", text)
    text = _SERIES_SUBTITLE.sub("", text)
    text = _SERIES_TAIL.sub("", text)
    text = _EDITION_WORDS.sub("", text)
    return _WS.sub(" ", text).strip() or title


class MatchStatus(StrEnum):
    MATCH = "match"
    AMBIGUOUS = "ambiguous"
    NO_MATCH = "no_match"


@dataclass(frozen=True)
class Candidate:
    book_id: str
    title: str
    author: str = ""


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: Candidate
    score: float
    title_score: float
    author_score: float


@dataclass(frozen=True)
class MatchResult:
    status: MatchStatus
    best: ScoredCandidate | None
    alternatives: tuple[ScoredCandidate, ...] = ()


def _strip_diacritics(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize_title(title: str) -> str:
    text = _FREE_PREFIX.sub("", title)  # strip FREE:/FREE STORY: before the subtitle cut
    text = _strip_diacritics(text).lower()
    text = re.sub(r"[:(].*$", " ", text)  # drop subtitle / parenthetical tail
    text = _TITLE_NOISE.sub(" ", text)
    text = _NON_ALNUM.sub(" ", text)
    return _WS.sub(" ", text).strip()


def normalize_author(author: str) -> str:
    text = _TRANSLATOR.sub("", author)
    text = _strip_diacritics(text).lower()
    text = _NON_ALNUM.sub(" ", text)
    return _WS.sub(" ", text).strip()


def _seq_ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _jaccard(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _containment(a: str, b: str) -> float:
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def title_similarity(a: str, b: str) -> float:
    return max(_seq_ratio(a, b), _jaccard(a, b))


def author_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return max(_seq_ratio(a, b), _containment(a, b))


def _combine(title_score: float, author_score: float, *, has_author: bool) -> float:
    if not has_author:
        return title_score
    return 0.7 * title_score + 0.3 * author_score


def score_candidate(title: str, author: str, candidate: Candidate) -> ScoredCandidate:
    nb_title = normalize_title(title)
    nb_author = normalize_author(author)
    ct_title = normalize_title(candidate.title)
    ct_author = normalize_author(candidate.author)
    title_score = title_similarity(nb_title, ct_title)
    author_score = author_similarity(nb_author, ct_author)
    score = _combine(title_score, author_score, has_author=bool(nb_author and ct_author))
    return ScoredCandidate(candidate, score, title_score, author_score)


def _same_work(a: Candidate, b: Candidate) -> bool:
    """Two candidates that are the same book (different editions/listings).

    A close-scoring runner-up with the same title and a matching-or-blank author is
    an edition/listing duplicate, not a genuinely different book, so it should not
    make the match ambiguous.
    """
    if normalize_title(a.title) != normalize_title(b.title):
        return False
    author_a, author_b = normalize_author(a.author), normalize_author(b.author)
    return author_a == author_b or not author_a or not author_b


def classify(scored: list[ScoredCandidate]) -> MatchResult:
    if not scored:
        return MatchResult(MatchStatus.NO_MATCH, None)

    ranked = sorted(scored, key=lambda s: s.score, reverse=True)
    best = ranked[0]
    alternatives = tuple(ranked[1:4])

    if best.score < MIN_PLAUSIBLE:
        return MatchResult(MatchStatus.NO_MATCH, None, alternatives)

    runner_up = ranked[1] if len(ranked) > 1 else None
    runner_up_score = runner_up.score if runner_up else 0.0
    decisive = (best.score - runner_up_score) >= AMBIGUOUS_MARGIN
    edition_tie = runner_up is not None and _same_work(best.candidate, runner_up.candidate)

    if best.score >= HIGH_CONFIDENCE and (decisive or edition_tie):
        return MatchResult(MatchStatus.MATCH, best, alternatives)

    return MatchResult(MatchStatus.AMBIGUOUS, best, alternatives)


def match_book(title: str, author: str, candidates: list[Candidate]) -> MatchResult:
    scored = [score_candidate(title, author, c) for c in candidates]
    return classify(scored)


_ISBN_SEP = re.compile(r"[\s-]+")


def normalize_isbn(value: str | None) -> str | None:
    """Strip hyphens/spaces (and any leftover whitespace) from an ISBN, or None if empty."""
    if not value:
        return None
    cleaned = _ISBN_SEP.sub("", value).strip()
    return cleaned or None


def is_isbn(value: str | None) -> bool:
    """True for a well-formed ISBN-10 or ISBN-13 (length + digits; ISBN-10 allows a trailing X).

    A format check only — it does not validate the check digit. Enough to decide whether a
    string is worth handing to StoryGraph search as an identifier rather than free text.
    """
    cleaned = normalize_isbn(value)
    if not cleaned:
        return False
    if len(cleaned) == 13:
        return cleaned.isdigit()
    if len(cleaned) == 10:
        return cleaned[:9].isdigit() and (cleaned[9].isdigit() or cleaned[9] in "Xx")
    return False
