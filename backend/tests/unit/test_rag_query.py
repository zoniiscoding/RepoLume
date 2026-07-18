"""Bounded stable question normalization and unsupported-scope classification."""

import pytest

from app.rag.query import QuestionPreprocessor, QuestionValidationError
from tests.conftest import make_settings


def test_question_normalization_preserves_identifiers_unicode_and_is_stable() -> None:
    processor = QuestionPreprocessor(make_settings())
    first = processor.prepare("  How\t does  café_service()\nwork?  ")
    second = processor.prepare("How does café_service() work?")

    assert first.text == "How does café_service() work?"
    assert first.fingerprint == second.fingerprint
    assert first.estimated_tokens == 7


@pytest.mark.parametrize(
    ("question", "overrides"),
    [
        (" \n\t ", {}),
        ("x?", {}),
        ("bad\x00question", {}),
        ("a" * 65, {"rag_question_max_bytes": 64}),
        ("a b c d e f g h i j k l m n o p q", {"rag_question_max_tokens": 16}),
    ],
)
def test_question_limits_fail_closed(question: str, overrides: dict[str, object]) -> None:
    with pytest.raises(QuestionValidationError):
        QuestionPreprocessor(make_settings(**overrides)).prepare(question)


@pytest.mark.parametrize(
    "question",
    [
        "Find all callers of process_job",
        "What is the current production runtime state?",
        "What is the latest Python release on the internet?",
    ],
)
def test_questions_requiring_later_milestones_are_classified_unsupported(question: str) -> None:
    processor = QuestionPreprocessor(make_settings())
    assert processor.is_unsupported(processor.prepare(question))


@pytest.mark.parametrize(
    "question",
    [
        "Why was this changed in commit history?",
        "What does commit abcdef123456 contain?",
        "Which pull request introduced validate?",
    ],
)
def test_history_questions_are_supported_in_milestone_7(question: str) -> None:
    processor = QuestionPreprocessor(make_settings())
    assert not processor.is_unsupported(processor.prepare(question))


def test_static_implementation_question_is_supported() -> None:
    processor = QuestionPreprocessor(make_settings())
    assert not processor.is_unsupported(
        processor.prepare("How does process_job validate an embedding response?")
    )
