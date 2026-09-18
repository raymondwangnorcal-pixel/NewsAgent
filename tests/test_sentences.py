"""Sentence-boundary judgement shared by the drafter and the renderers."""
from __future__ import annotations

import pytest

from news_agent.sentences import ends_sentence, split_sentences


@pytest.mark.parametrize(
    "text",
    [
        "Children’s entertainer Ms. Rachel is releasing an album.",
        "The U.S. Senate voted to advance the bill.",
        "Robert F. Kennedy Jr. said the agency would revisit the guidance.",
        "Acme Corp. and Beta Inc. agreed to merge.",
        "St. Louis officials approved the new transit line.",
        "Trading opened at 9 a.m. Eastern and stayed volatile.",
    ],
)
def test_abbreviations_acronyms_and_initials_stay_in_one_sentence(text: str) -> None:
    assert split_sentences(text) == [text]


def test_ordinary_sentences_still_split() -> None:
    assert split_sentences("The board met. A vote followed. It passed.") == [
        "The board met.",
        "A vote followed.",
        "It passed.",
    ]


def test_exclamation_and_question_marks_always_end_a_sentence() -> None:
    assert split_sentences("Did the board meet? It did. What a day!") == [
        "Did the board meet?",
        "It did.",
        "What a day!",
    ]


def test_split_preserves_every_word() -> None:
    text = "Ms. Rachel sang. Mr. Aron played. The crowd left."
    assert " ".join(split_sentences(text)) == text


def test_ends_sentence_reads_the_punctuation_at_the_given_index() -> None:
    text = "Ms. Rachel sang."
    assert ends_sentence(text, text.index(".")) is False
    assert ends_sentence(text, len(text) - 1) is True
