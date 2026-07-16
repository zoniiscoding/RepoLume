"""Deterministic evidence selection and server-owned citation validation."""

from app.llm.client import (
    DraftAnswerability,
    DraftUncertainty,
    GroundedAnswerDraft,
)
from app.rag.evidence import CitationValidator, EvidenceSelector
from app.rag.models import Answerability
from app.vector.qdrant import RetrievalHit
from tests.conftest import make_settings


def hit(
    *,
    score: float,
    file_path: str,
    start: int,
    end: int,
    stable_hash: str,
    content: str = "def service(): pass",
) -> RetrievalHit:
    return RetrievalHit(
        score=score,
        file_path=file_path,
        language="python",
        chunk_type="function",
        symbol_name="service",
        qualified_symbol_name="app.service",
        start_line=start,
        end_line=end,
        content=content,
        stable_chunk_hash=stable_hash,
    )


def test_selection_is_stable_deduplicated_nonoverlapping_and_bounded() -> None:
    selector = EvidenceSelector(
        make_settings(
            rag_retrieval_top_k=2,
            rag_max_evidence_per_file=1,
            rag_max_evidence_bytes=1024,
            rag_max_evidence_item_bytes=512,
        )
    )
    selected = selector.select(
        (
            hit(score=0.8, file_path="b.py", start=10, end=20, stable_hash="b" * 64),
            hit(score=0.9, file_path="a.py", start=1, end=5, stable_hash="a" * 64),
            hit(score=0.85, file_path="a.py", start=4, end=8, stable_hash="c" * 64),
            hit(score=0.7, file_path="c.py", start=1, end=2, stable_hash="a" * 64),
        )
    )

    assert [(item.evidence_id, item.file_path) for item in selected] == [
        ("E1", "a.py"),
        ("E2", "b.py"),
    ]


def test_oversized_evidence_is_rejected_not_truncated() -> None:
    selector = EvidenceSelector(
        make_settings(rag_max_evidence_bytes=1024, rag_max_evidence_item_bytes=512)
    )
    assert (
        selector.select(
            (
                hit(
                    score=1.0,
                    file_path="large.py",
                    start=1,
                    end=1,
                    stable_hash="a" * 64,
                    content="x" * 513,
                ),
            )
        )
        == ()
    )


def test_citations_resolve_only_known_server_evidence_ids() -> None:
    evidence = EvidenceSelector(make_settings()).select(
        (hit(score=0.9, file_path="a.py", start=1, end=5, stable_hash="a" * 64),)
    )
    validator = CitationValidator()
    valid = GroundedAnswerDraft(
        answer="Supported.",
        answerability=DraftAnswerability.ANSWERED,
        uncertainty=DraftUncertainty.LOW,
        evidence_ids=["E1", "E1"],
    )
    state, citations = validator.validate(valid, evidence, commit_sha="a" * 40)
    assert state is Answerability.ANSWERED
    assert len(citations) == 1
    assert citations[0].file_path == "a.py"
    assert citations[0].qualified_symbol_name == "app.service"
    assert citations[0].commit_sha == "a" * 40
    assert citations[0].supporting_excerpt == "def service(): pass"

    unknown = valid.model_copy(update={"evidence_ids": ["E999"]})
    assert validator.validate(unknown, evidence, commit_sha="a" * 40) == (
        Answerability.INSUFFICIENT_EVIDENCE,
        (),
    )
    altered_inline = valid.model_copy(update={"answer": "Invented citation [E999]."})
    assert validator.validate(altered_inline, evidence, commit_sha="a" * 40) == (
        Answerability.INSUFFICIENT_EVIDENCE,
        (),
    )


def test_answered_without_citation_and_nonanswer_with_citation_fail_closed() -> None:
    evidence = EvidenceSelector(make_settings()).select(
        (hit(score=0.9, file_path="a.py", start=1, end=5, stable_hash="a" * 64),)
    )
    validator = CitationValidator()
    uncited = GroundedAnswerDraft(
        answer="Unsupported claim.",
        answerability=DraftAnswerability.ANSWERED,
        uncertainty=DraftUncertainty.HIGH,
        evidence_ids=[],
    )
    assert validator.validate(uncited, evidence, commit_sha="a" * 40) == (
        Answerability.INSUFFICIENT_EVIDENCE,
        (),
    )
    refusal = uncited.model_copy(
        update={
            "answerability": DraftAnswerability.INSUFFICIENT_EVIDENCE,
            "evidence_ids": ["E1"],
        }
    )
    assert validator.validate(refusal, evidence, commit_sha="a" * 40) == (
        Answerability.INSUFFICIENT_EVIDENCE,
        (),
    )
