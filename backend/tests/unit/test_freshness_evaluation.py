from pathlib import Path

import pytest

from app.indexing.evaluation import (
    FreshnessCase,
    FreshnessObservation,
    calculate_freshness_metrics,
    load_cases,
    load_observations,
)


def test_milestone9_corpus_has_required_breadth_and_content_free_metrics() -> None:
    root = Path(__file__).parents[2] / "evaluation"
    cases = load_cases(root / "milestone9_cases.json")
    observations = load_observations(root / "milestone9_fixture_observations.json")
    metrics = calculate_freshness_metrics(cases, observations)

    assert len(cases) == 30
    assert len({case.category for case in cases}) == 30
    assert metrics.case_count == 30
    assert metrics.observation_count == 31
    assert metrics.fixture_observation_count == 31
    assert metrics.changed_file_classification_accuracy == 1.0
    assert metrics.mode_selection_accuracy == 1.0
    assert metrics.delivery_state_accuracy == 1.0
    assert metrics.activation_accuracy == 1.0
    assert metrics.active_preservation_accuracy == 1.0
    assert metrics.retry_classification_accuracy == 1.0
    assert metrics.graph_freshness_accuracy == 1.0
    assert metrics.citation_freshness_accuracy == 1.0
    assert metrics.deterministic_consistency == 1.0
    assert metrics.old_version_leakage_count == 0
    assert metrics.cross_repository_leakage_count == 0
    assert metrics.mean_latency_ms is None
    assert metrics.max_latency_ms is None


def test_freshness_evaluation_rejects_invalid_observation_sets() -> None:
    case = FreshnessCase.model_validate(
        {
            "case_id": "known",
            "category": "known contract",
            "expected_delivery_status": "completed",
            "expects_activation": True,
            "expects_active_preserved": True,
        }
    )
    unknown = FreshnessObservation.model_validate(
        {
            "case_id": "unknown",
            "delivery_status": "completed",
            "activated": True,
            "active_preserved": True,
            "response_fingerprint": "bounded",
        }
    )

    with pytest.raises(ValueError, match="missing_observations"):
        calculate_freshness_metrics([case], [])
    with pytest.raises(ValueError, match="unknown_observation_case"):
        calculate_freshness_metrics([case], [unknown])
    with pytest.raises(ValueError, match="duplicate_case_id"):
        calculate_freshness_metrics([case, case], [unknown])
