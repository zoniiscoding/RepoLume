"""Content-free retrieval, tool-selection, and grounding metric harness."""

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent.models import AgentToolName
from app.rag.models import Answerability

CitationType = Literal["code", "commit", "pull_request", "caller"]


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
    expected_tools: list[AgentToolName] = Field(default_factory=list)
    expected_citation_types: list[CitationType] = Field(default_factory=list)
    expects_history_evidence: bool = False
    expected_callers: list[str] = Field(default_factory=list)
    expects_ambiguous_target: bool = False
    expects_unresolved_call: bool = False


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
    latency_ms: float | None = Field(default=None, ge=0)
    response_fingerprint: str
    tool_names: list[AgentToolName] = Field(default_factory=list)
    citation_types: list[CitationType] = Field(default_factory=list)
    relevant_history_evidence_count: int = Field(default=0, ge=0)
    tool_limit_violation: bool = False
    unknown_tool_execution: bool = False
    measurement_kind: Literal["observed", "fixture_contract"] = "observed"
    retrieved_callers: list[str] = Field(default_factory=list)
    exact_edge_count: int = Field(default=0, ge=0)
    correct_exact_edge_count: int = Field(default=0, ge=0)
    ambiguity_classification_correct: bool | None = None
    unresolved_classification_correct: bool | None = None
    inactive_graph_leakage: bool = False
    fabricated_caller_citation: bool = False


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
    mean_latency_ms: float | None
    max_latency_ms: float | None
    tool_selection_accuracy: float
    citation_type_accuracy: float
    history_evidence_recall: float
    unsupported_question_accuracy: float
    tool_limit_violation_count: int
    unknown_tool_execution_count: int
    fixture_observation_count: int
    caller_precision: float
    caller_recall: float
    exact_edge_precision: float
    ambiguity_accuracy: float
    unresolved_accuracy: float
    inactive_graph_leakage_count: int
    fabricated_caller_citation_count: int


@dataclass(frozen=True, slots=True)
class _AgentMetrics:
    tool_selection_accuracy: float
    citation_type_accuracy: float
    history_evidence_recall: float
    unsupported_question_accuracy: float
    tool_limit_violation_count: int
    unknown_tool_execution_count: int
    fixture_observation_count: int


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
    latencies = [
        observation.latency_ms for observation in observations if observation.latency_ms is not None
    ]
    agent = _calculate_agent_metrics(case_by_id, observations)
    expected_callers = sum(len(case_by_id[item.case_id].expected_callers) for item in observations)
    retrieved_callers = sum(len(item.retrieved_callers) for item in observations)
    correct_callers = sum(
        len(set(item.retrieved_callers) & set(case_by_id[item.case_id].expected_callers))
        for item in observations
    )
    exact_edges = sum(item.exact_edge_count for item in observations)
    correct_exact_edges = sum(item.correct_exact_edge_count for item in observations)
    ambiguity_items = [
        item for item in observations if case_by_id[item.case_id].expects_ambiguous_target
    ]
    unresolved_items = [
        item for item in observations if case_by_id[item.case_id].expects_unresolved_call
    ]
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
        mean_latency_ms=sum(latencies) / len(latencies) if latencies else None,
        max_latency_ms=max(latencies) if latencies else None,
        tool_selection_accuracy=agent.tool_selection_accuracy,
        citation_type_accuracy=agent.citation_type_accuracy,
        history_evidence_recall=agent.history_evidence_recall,
        unsupported_question_accuracy=agent.unsupported_question_accuracy,
        tool_limit_violation_count=agent.tool_limit_violation_count,
        unknown_tool_execution_count=agent.unknown_tool_execution_count,
        fixture_observation_count=agent.fixture_observation_count,
        caller_precision=1.0 if retrieved_callers == 0 else correct_callers / retrieved_callers,
        caller_recall=1.0 if expected_callers == 0 else correct_callers / expected_callers,
        exact_edge_precision=1.0 if exact_edges == 0 else correct_exact_edges / exact_edges,
        ambiguity_accuracy=(
            1.0
            if not ambiguity_items
            else sum(item.ambiguity_classification_correct is True for item in ambiguity_items)
            / len(ambiguity_items)
        ),
        unresolved_accuracy=(
            1.0
            if not unresolved_items
            else sum(item.unresolved_classification_correct is True for item in unresolved_items)
            / len(unresolved_items)
        ),
        inactive_graph_leakage_count=sum(item.inactive_graph_leakage for item in observations),
        fabricated_caller_citation_count=sum(
            item.fabricated_caller_citation for item in observations
        ),
    )


def _calculate_agent_metrics(
    cases: dict[str, EvaluationCase], observations: list[EvaluationObservation]
) -> _AgentMetrics:
    tool_items = [item for item in observations if cases[item.case_id].expected_tools]
    citation_items = [item for item in observations if cases[item.case_id].expected_citation_types]
    history_items = [item for item in observations if cases[item.case_id].expects_history_evidence]
    unsupported_items = [
        item
        for item in observations
        if cases[item.case_id].expected_answerability is Answerability.UNSUPPORTED_QUESTION
    ]
    return _AgentMetrics(
        tool_selection_accuracy=(
            1.0
            if not tool_items
            else sum(item.tool_names == cases[item.case_id].expected_tools for item in tool_items)
            / len(tool_items)
        ),
        citation_type_accuracy=(
            1.0
            if not citation_items
            else sum(
                set(cases[item.case_id].expected_citation_types).issubset(item.citation_types)
                for item in citation_items
            )
            / len(citation_items)
        ),
        history_evidence_recall=(
            1.0
            if not history_items
            else sum(item.relevant_history_evidence_count > 0 for item in history_items)
            / len(history_items)
        ),
        unsupported_question_accuracy=(
            1.0
            if not unsupported_items
            else sum(
                item.answerability is Answerability.UNSUPPORTED_QUESTION
                for item in unsupported_items
            )
            / len(unsupported_items)
        ),
        tool_limit_violation_count=sum(item.tool_limit_violation for item in observations),
        unknown_tool_execution_count=sum(item.unknown_tool_execution for item in observations),
        fixture_observation_count=sum(
            item.measurement_kind == "fixture_contract" for item in observations
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate RepoLume evaluation metrics")
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
