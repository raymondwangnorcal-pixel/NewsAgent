"""Sentence-boundary judgement shared by the drafter and the renderers.

A period is not always the end of a sentence: courtesy titles ("Ms."), ranks,
company suffixes ("Inc."), dotted acronyms ("U.S.", "a.m.") and middle initials
all take one. The fallback paragraph trimmer and the headline splitter both
need that judgement, and they need the *same* one, so it lives here rather than
being spelled out twice with two abbreviation lists that drift apart.
"""
from __future__ import annotations

import re

# Tokens that take a trailing period without ending a sentence. Stored
# lower-case and without the dot; compared against the word before the period.
ABBREVIATIONS = frozenset(
    {
        "adm", "al", "approx", "assn", "atty", "ave", "blvd", "bros", "capt",
        "cf", "cmdr", "co", "col", "corp", "dept", "det", "dist", "div", "dr",
        "ed", "eds", "eg", "est", "etc", "fig", "ft", "gen", "gov", "hon",
        "ie", "inc", "jr", "lt", "ltd", "maj", "messrs", "mr", "mrs", "ms",
        "mt", "mx", "no", "pp", "pres", "prof", "rd", "rep", "rev", "sen",
        "sgt", "sq", "sr", "st", "supt", "univ", "vol", "vs",
    }
)

# The run of letters and dots immediately before a candidate sentence end.
_TRAILING_TOKEN_RE = re.compile(r"[A-Za-z.]+$")

# Whitespace following terminal punctuation: every *candidate* boundary.
_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


def ends_sentence(text: str, stop_index: int) -> bool:
    """Whether the terminal punctuation at *stop_index* closes a sentence.

    Only periods are ambiguous. A period is not a sentence end when it closes
    an abbreviation ("Ms.", "Inc."), a dotted acronym ("U.S.", "a.m.") or a
    middle initial ("Robert F. Kennedy").
    """
    if text[stop_index] != ".":
        return True
    token = _TRAILING_TOKEN_RE.search(text[:stop_index])
    if token is None:
        return True
    word = token.group()
    if "." in word:
        return False
    if len(word) == 1 and word.isupper():
        return False
    return word.lower() not in ABBREVIATIONS


def split_sentences(text: str) -> list[str]:
    """Split *text* into sentences, keeping abbreviations with their sentence.

    The inverse of ``" ".join`` on the result is the original text up to the
    whitespace runs that separated the sentences.
    """
    sentences: list[str] = []
    start = 0
    for match in _BOUNDARY_RE.finditer(text):
        if not ends_sentence(text, match.start() - 1):
            continue
        sentences.append(text[start:match.start()])
        start = match.end()
    tail = text[start:]
    if tail:
        sentences.append(tail)
    return sentences
