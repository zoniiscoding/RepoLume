"""Metric harness calculates transparent retrieval and grounding measures."""

import json
from pathlib import Path

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
            expected_tools=["search_code"],
            expected_citation_types=["code"],
            expects_history_evidence=False,
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
            tool_names=["search_code"],
            citation_types=["code"],
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
            tool_names=["search_code"],
            citation_types=["code"],
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
    assert metrics.tool_selection_accuracy == 1
    assert metrics.citation_type_accuracy == 1
    assert metrics.history_evidence_recall == 1
    assert metrics.unsupported_question_accuracy == 1
    assert metrics.tool_limit_violation_count == 0
    assert metrics.unknown_tool_execution_count == 0
    assert metrics.fixture_observation_count == 0
    assert metrics.caller_precision == 1
    assert metrics.caller_recall == 1
    assert metrics.exact_edge_precision == 1


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


def test_milestone7_dataset_has_required_agent_history_and_security_coverage() -> None:
    path = Path(__file__).parents[2] / "evaluation" / "milestone7_cases.json"
    cases = [EvaluationCase.model_validate(item) for item in json.loads(path.read_text())]
    categories = {item.category for item in cases}

    assert len(cases) >= 20
    assert len({item.case_id for item in cases}) == len(cases)
    assert {
        "code_only",
        "history_only",
        "mixed_code_commit",
        "commit_prompt_injection",
        "patch_prompt_injection",
        "pr_prompt_injection",
        "cross_repository_history",
        "fabricated_commit_citation",
        "fabricated_pr_citation",
        "caller_milestone_8",
        "file_change_history",
        "cross_repository_pr",
        "inactive_index_code",
        "unknown_tool_attempt",
        "revocation_during_tool",
    }.issubset(categories)
    assert any(item.expected_tools == ["search_code", "get_history"] for item in cases)


def test_milestone7_fixture_contract_metrics_are_explicit_and_content_free() -> None:
    root = Path(__file__).parents[2] / "evaluation"
    cases = [
        EvaluationCase.model_validate(item)
        for item in json.loads((root / "milestone7_cases.json").read_text())
    ]
    observations = [
        EvaluationObservation.model_validate(item)
        for item in json.loads((root / "milestone7_fixture_observations.json").read_text())
    ]

    metrics = calculate_metrics(cases, observations)

    assert metrics.case_count == 27
    assert metrics.observation_count == 27
    assert metrics.fixture_observation_count == 27
    assert metrics.recall_at_k == 1
    assert metrics.history_evidence_recall == 1
    assert metrics.citation_precision == 1
    assert metrics.citation_validity == 1
    assert metrics.no_answer_accuracy == 1
    assert metrics.unsupported_question_accuracy == 1
    assert metrics.cross_repository_leakage_count == 0
    assert metrics.tool_limit_violation_count == 0
    assert metrics.unknown_tool_execution_count == 0
    assert metrics.tool_selection_accuracy == 1
    assert metrics.citation_type_accuracy == 1
    assert metrics.mean_latency_ms is None
    assert metrics.max_latency_ms is None


def test_milestone8_fixture_contract_measures_call_graph_quality_and_isolation() -> None:
    root = Path(__file__).parents[2] / "evaluation"
    cases = [
        EvaluationCase.model_validate(item)
        for item in json.loads((root / "milestone8_cases.json").read_text())
    ]
    observations = [
        EvaluationObservation.model_validate(item)
        for item in json.loads((root / "milestone8_fixture_observations.json").read_text())
    ]

    metrics = calculate_metrics(cases, observations)

    assert metrics.case_count == 20
    assert metrics.fixture_observation_count == 20
    assert metrics.caller_precision == 1
    assert metrics.caller_recall == 1
    assert metrics.exact_edge_precision == 1
    assert metrics.ambiguity_accuracy == 1
    assert metrics.unresolved_accuracy == 1
    assert metrics.inactive_graph_leakage_count == 0
    assert metrics.fabricated_caller_citation_count == 0
    assert metrics.mean_latency_ms is None
