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
    r"\b(unabridged|a novel|dramatized adaptation|graphic audio|audio ?book)\b",
    re.IGNORECASE,
)
_TRANSLATOR = re.compile(r"\s*-\s*translator\b", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


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
    text = _strip_diacritics(title).lower()
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


def classify(scored: list[ScoredCandidate]) -> MatchResult:
    if not scored:
        return MatchResult(MatchStatus.NO_MATCH, None)

    ranked = sorted(scored, key=lambda s: s.score, reverse=True)
    best = ranked[0]
    alternatives = tuple(ranked[1:4])

    if best.score < MIN_PLAUSIBLE:
        return MatchResult(MatchStatus.NO_MATCH, None, alternatives)

    runner_up = ranked[1].score if len(ranked) > 1 else 0.0
    if best.score >= HIGH_CONFIDENCE and (best.score - runner_up) >= AMBIGUOUS_MARGIN:
        return MatchResult(MatchStatus.MATCH, best, alternatives)

    return MatchResult(MatchStatus.AMBIGUOUS, best, alternatives)


def match_book(title: str, author: str, candidates: list[Candidate]) -> MatchResult:
    scored = [score_candidate(title, author, c) for c in candidates]
    return classify(scored)
