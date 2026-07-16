"""Metric harness calculates transparent retrieval and grounding measures."""

import pytest

from app.rag.evaluation import (
    EvaluationCase,
    EvaluationObservation,
    calculate_metrics,
)


def test_evaluation_metrics_cover_retrieval_citations_refusal_leakage_and_latency() -> None:
    cases = [
        EvaluationCase(
            case_id="answered",
            category="symbol",
            question="Where?",
            expected_paths=["app.py"],
            expected_answerability="answered",
        ),
        EvaluationCase(
            case_id="unsupported",
            category="runtime",
            question="Runtime?",
            expected_paths=[],
            expected_answerability="unsupported_question",
        ),
    ]
    observations = [
        EvaluationObservation(
            case_id="answered",
            retrieved_paths=["app.py"],
            citation_count=2,
            valid_citation_count=2,
            supported_citation_count=1,
            answerability="answered",
            material_claim_count=2,
            unsupported_claim_count=0,
            cross_repository_leakage=False,
            latency_ms=10,
            response_fingerprint="same",
        ),
        EvaluationObservation(
            case_id="answered",
            retrieved_paths=["app.py"],
            citation_count=1,
            valid_citation_count=1,
            answerability="answered",
            material_claim_count=1,
            unsupported_claim_count=0,
            cross_repository_leakage=False,
            latency_ms=20,
            response_fingerprint="same",
        ),
        EvaluationObservation(
            case_id="unsupported",
            retrieved_paths=[],
            citation_count=0,
            valid_citation_count=0,
            answerability="unsupported_question",
            material_claim_count=0,
            unsupported_claim_count=0,
            cross_repository_leakage=False,
            latency_ms=5,
            response_fingerprint="unsupported",
        ),
    ]

    metrics = calculate_metrics(cases, observations)
    assert metrics.recall_at_k == 1
    assert metrics.citation_precision == pytest.approx(2 / 3)
    assert metrics.citation_validity == 1
    assert metrics.no_answer_accuracy == 1
    assert metrics.cross_repository_leakage_count == 0
    assert metrics.unsupported_claim_rate == 0
    assert metrics.deterministic_consistency == 1
    assert metrics.mean_latency_ms == pytest.approx(35 / 3)
    assert metrics.max_latency_ms == 20


def test_evaluation_rejects_unknown_cases_and_empty_observations() -> None:
    case = EvaluationCase(
        case_id="one",
        category="test",
        question="Question?",
        expected_paths=[],
        expected_answerability="insufficient_evidence",
    )
    with pytest.raises(ValueError, match="missing_observations"):
        calculate_metrics([case], [])
    unknown = EvaluationObservation(
        case_id="unknown",
        retrieved_paths=[],
        citation_count=0,
        valid_citation_count=0,
        answerability="insufficient_evidence",
        material_claim_count=0,
        unsupported_claim_count=0,
        cross_repository_leakage=False,
        latency_ms=0,
        response_fingerprint="x",
    )
    with pytest.raises(ValueError, match="unknown_observation_case"):
        calculate_metrics([case], [unknown])
