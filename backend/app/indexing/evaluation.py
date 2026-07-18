"""Content-free Milestone 9 freshness contract metrics."""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.enums import IndexingMode, WebhookDeliveryStatus


class FreshnessCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    category: str
    expected_mode: IndexingMode | None = None
    expected_delivery_status: WebhookDeliveryStatus
    expected_changed_counts: dict[str, int] = Field(default_factory=dict)
    expects_activation: bool
    expects_active_preserved: bool
    expected_retryable: bool = False


class FreshnessObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    actual_mode: IndexingMode | None = None
    delivery_status: WebhookDeliveryStatus
    changed_counts: dict[str, int] = Field(default_factory=dict)
    activated: bool
    active_preserved: bool
    retryable: bool = False
    old_version_leakage: bool = False
    cross_repository_leakage: bool = False
    graph_fresh: bool = True
    citation_fresh: bool = True
    response_fingerprint: str
    latency_ms: float | None = Field(default=None, ge=0)
    measurement_kind: Literal["observed", "fixture_contract"] = "observed"


class FreshnessMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_count: int
    observation_count: int
    changed_file_classification_accuracy: float
    mode_selection_accuracy: float
    delivery_state_accuracy: float
    activation_accuracy: float
    active_preservation_accuracy: float
    retry_classification_accuracy: float
    graph_freshness_accuracy: float
    citation_freshness_accuracy: float
    deterministic_consistency: float
    old_version_leakage_count: int
    cross_repository_leakage_count: int
    fixture_observation_count: int
    mean_latency_ms: float | None
    max_latency_ms: float | None


def calculate_freshness_metrics(
    cases: list[FreshnessCase], observations: list[FreshnessObservation]
) -> FreshnessMetrics:
    case_by_id = {item.case_id: item for item in cases}
    if len(case_by_id) != len(cases):
        raise ValueError("duplicate_case_id")
    if not observations:
        raise ValueError("missing_observations")
    if any(item.case_id not in case_by_id for item in observations):
        raise ValueError("unknown_observation_case")
    fingerprints: defaultdict[str, set[str]] = defaultdict(set)
    for item in observations:
        fingerprints[item.case_id].add(item.response_fingerprint)
    mode_items = [item for item in observations if case_by_id[item.case_id].expected_mode]
    latencies = [item.latency_ms for item in observations if item.latency_ms is not None]
    count = len(observations)
    return FreshnessMetrics(
        case_count=len(cases),
        observation_count=count,
        changed_file_classification_accuracy=sum(
            item.changed_counts == case_by_id[item.case_id].expected_changed_counts
            for item in observations
        )
        / count,
        mode_selection_accuracy=(
            1.0
            if not mode_items
            else sum(
                item.actual_mode is case_by_id[item.case_id].expected_mode for item in mode_items
            )
            / len(mode_items)
        ),
        delivery_state_accuracy=sum(
            item.delivery_status is case_by_id[item.case_id].expected_delivery_status
            for item in observations
        )
        / count,
        activation_accuracy=sum(
            item.activated is case_by_id[item.case_id].expects_activation for item in observations
        )
        / count,
        active_preservation_accuracy=sum(
            item.active_preserved is case_by_id[item.case_id].expects_active_preserved
            for item in observations
        )
        / count,
        retry_classification_accuracy=sum(
            item.retryable is case_by_id[item.case_id].expected_retryable for item in observations
        )
        / count,
        graph_freshness_accuracy=sum(item.graph_fresh for item in observations) / count,
        citation_freshness_accuracy=sum(item.citation_fresh for item in observations) / count,
        deterministic_consistency=sum(len(values) == 1 for values in fingerprints.values())
        / len(fingerprints),
        old_version_leakage_count=sum(item.old_version_leakage for item in observations),
        cross_repository_leakage_count=sum(item.cross_repository_leakage for item in observations),
        fixture_observation_count=sum(
            item.measurement_kind == "fixture_contract" for item in observations
        ),
        mean_latency_ms=sum(latencies) / len(latencies) if latencies else None,
        max_latency_ms=max(latencies) if latencies else None,
    )


def load_cases(path: Path) -> list[FreshnessCase]:
    return [FreshnessCase.model_validate(item) for item in json.loads(path.read_text())]


def load_observations(path: Path) -> list[FreshnessObservation]:
    return [FreshnessObservation.model_validate(item) for item in json.loads(path.read_text())]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    arguments = parser.parse_args()
    metrics = calculate_freshness_metrics(
        load_cases(arguments.cases), load_observations(arguments.observations)
    )
    sys.stdout.write(f"{metrics.model_dump_json(indent=2)}\n")


if __name__ == "__main__":
    main()
