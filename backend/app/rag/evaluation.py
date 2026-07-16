"""Content-free Milestone 6 retrieval and grounded-answer metric harness."""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.rag.models import Answerability


class ExpectedCitationRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    category: str
    question: str
    expected_paths: list[str]
    expected_symbols: list[str] = Field(default_factory=list)
    expected_ranges: list[ExpectedCitationRange] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    unsupported_category: str | None = None
    expected_answerability: Answerability


class EvaluationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    retrieved_paths: list[str]
    citation_count: int = Field(ge=0)
    valid_citation_count: int = Field(ge=0)
    supported_citation_count: int | None = Field(default=None, ge=0)
    answerability: Answerability
    material_claim_count: int = Field(ge=0)
    unsupported_claim_count: int = Field(ge=0)
    cross_repository_leakage: bool
    latency_ms: float = Field(ge=0)
    response_fingerprint: str


class EvaluationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_count: int
    observation_count: int
    recall_at_k: float
    citation_precision: float
    citation_validity: float
    no_answer_accuracy: float
    cross_repository_leakage_count: int
    unsupported_claim_rate: float
    deterministic_consistency: float
    mean_latency_ms: float
    max_latency_ms: float


def calculate_metrics(
    cases: list[EvaluationCase], observations: list[EvaluationObservation]
) -> EvaluationMetrics:
    """Calculate transparent metrics without grading exact answer wording."""
    case_by_id = {case.case_id: case for case in cases}
    if len(case_by_id) != len(cases):
        raise ValueError("duplicate_case_id")
    if any(observation.case_id not in case_by_id for observation in observations):
        raise ValueError("unknown_observation_case")
    if not observations:
        raise ValueError("missing_observations")

    relevant = 0
    cited = 0
    valid_cited = 0
    supported_cited = 0
    no_answer_total = 0
    no_answer_correct = 0
    claims = 0
    unsupported_claims = 0
    fingerprints: defaultdict[str, set[str]] = defaultdict(set)
    for observation in observations:
        case = case_by_id[observation.case_id]
        if not case.expected_paths or set(case.expected_paths) & set(observation.retrieved_paths):
            relevant += 1
        cited += observation.citation_count
        valid_cited += observation.valid_citation_count
        supported_cited += (
            observation.valid_citation_count
            if observation.supported_citation_count is None
            else observation.supported_citation_count
        )
        if case.expected_answerability is not Answerability.ANSWERED:
            no_answer_total += 1
            no_answer_correct += observation.answerability is case.expected_answerability
        claims += observation.material_claim_count
        unsupported_claims += observation.unsupported_claim_count
        fingerprints[observation.case_id].add(observation.response_fingerprint)

    repeat_groups = [values for values in fingerprints.values() if len(values) >= 1]
    consistent = sum(len(values) == 1 for values in repeat_groups)
    latencies = [observation.latency_ms for observation in observations]
    return EvaluationMetrics(
        case_count=len(cases),
        observation_count=len(observations),
        recall_at_k=relevant / len(observations),
        citation_precision=1.0 if cited == 0 else supported_cited / cited,
        citation_validity=1.0 if cited == 0 else valid_cited / cited,
        no_answer_accuracy=1.0 if no_answer_total == 0 else no_answer_correct / no_answer_total,
        cross_repository_leakage_count=sum(
            observation.cross_repository_leakage
            or bool(
                set(case_by_id[observation.case_id].forbidden_paths)
                & set(observation.retrieved_paths)
            )
            for observation in observations
        ),
        unsupported_claim_rate=0.0 if claims == 0 else unsupported_claims / claims,
        deterministic_consistency=consistent / len(repeat_groups),
        mean_latency_ms=sum(latencies) / len(latencies),
        max_latency_ms=max(latencies),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate RepoLume Milestone 6 metrics")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--observations", type=Path, required=True)
    arguments = parser.parse_args()
    cases = [
        EvaluationCase.model_validate(item) for item in json.loads(arguments.cases.read_text())
    ]
    observations = [
        EvaluationObservation.model_validate(item)
        for item in json.loads(arguments.observations.read_text())
    ]
    sys.stdout.write(calculate_metrics(cases, observations).model_dump_json(indent=2) + "\n")


if __name__ == "__main__":
    main()
