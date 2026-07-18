"""Bounded orchestration, failure, citation, and cancellation tests."""

import asyncio
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from app.agent.models import (
    AgentDecision,
    AgentGenerationRequest,
    AgentStepStatus,
    AgentToolName,
    CommitEvidence,
    PullRequestEvidence,
)
from app.agent.service import ActiveIndex, AgentQuestionService
from app.agent.tools import ActiveRepositoryContext, AgentToolError, AgentToolRegistry
from app.db.models.enums import RepositoryIndexingStatus
from app.db.models.repository import Repository
from app.rag.models import Answerability, AnswerUncertainty, Evidence
from app.services.installations import InstallationAccessError
from tests.conftest import make_settings


def repository() -> Repository:
    return Repository(
        id=uuid.uuid4(),
        installation_id=uuid.uuid4(),
        github_repository_id=77,
        github_owner="owner",
        github_name="repo",
        github_full_name="owner/repo",
        github_url="https://github.com/owner/repo",
        is_private=True,
        default_branch="main",
        last_indexed_commit_sha="a" * 40,
        index_version=1,
        indexing_status=RepositoryIndexingStatus.COMPLETE,
        indexing_progress=100,
        active_vector_count=1,
    )


class FakeInstallations:
    def __init__(self, current: Repository, *, revoke_after: int | None = None) -> None:
        self.current = current
        self.calls = 0
        self.revoke_after = revoke_after

    async def get_authorized_repository(self, **kwargs: object) -> Repository:
        self.calls += 1
        if self.revoke_after is not None and self.calls > self.revoke_after:
            raise InstallationAccessError
        assert kwargs["repository_id"] == self.current.id
        return self.current


class ScriptedProvider:
    def __init__(self, decisions: list[AgentDecision], *, delay: float = 0) -> None:
        self.decisions = decisions
        self.delay = delay
        self.requests: list[AgentGenerationRequest] = []

    async def decide(self, request: AgentGenerationRequest) -> AgentDecision:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.decisions.pop(0)


class FakeTool:
    def __init__(
        self,
        name: AgentToolName,
        results: tuple[Evidence | CommitEvidence | PullRequestEvidence, ...] = (),
        *,
        delay: float = 0,
        failure: str | None = None,
    ) -> None:
        self.name = name
        self.results = results
        self.delay = delay
        self.failure = failure
        self.calls: list[tuple[ActiveRepositoryContext, Mapping[str, str], int]] = []

    async def execute(
        self,
        context: ActiveRepositoryContext,
        arguments: Mapping[str, str],
        *,
        step: int,
    ) -> tuple[Evidence | CommitEvidence | PullRequestEvidence, ...]:
        self.calls.append((context, arguments, step))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.failure:
            raise AgentToolError(self.failure)
        return self.results


class CancellableTool(FakeTool):
    def __init__(self, name: AgentToolName) -> None:
        super().__init__(name)
        self.started = asyncio.Event()
        self.cancelled = False

    async def execute(
        self,
        context: ActiveRepositoryContext,
        arguments: Mapping[str, str],
        *,
        step: int,
    ) -> tuple[Evidence | CommitEvidence | PullRequestEvidence, ...]:
        self.calls.append((context, arguments, step))
        self.started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return ()


def code_evidence(evidence_id: str = "T1-C1") -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        score=0.9,
        file_path="app/service.py",
        language="python",
        chunk_type="function",
        symbol_name="validate",
        qualified_symbol_name="app.service.validate",
        start_line=1,
        end_line=2,
        stable_chunk_hash="c" * 64,
        content="def validate(value): return bool(value)",
    )


def commit_evidence(evidence_id: str = "T2-H1") -> CommitEvidence:
    return CommitEvidence(
        evidence_id=evidence_id,
        commit_sha="d" * 40,
        message="Add validation",
        committed_at=datetime(2026, 1, 1, tzinfo=UTC),
        author_login="developer",
        parent_shas=("c" * 40,),
        changed_paths=("app/service.py",),
        patch_excerpt="+def validate(value)",
        html_url=f"https://github.com/owner/repo/commit/{'d' * 40}",
    )


def tool_decision(name: AgentToolName, query: str = "validate") -> AgentDecision:
    return AgentDecision(action="tool", tool_name=name, arguments={"query": query})


def final_decision(ids: list[str], state: Answerability = Answerability.ANSWERED) -> AgentDecision:
    return AgentDecision(
        action="final",
        answer="Validation is implemented and was added in the cited change.",
        answerability=state,
        uncertainty=AnswerUncertainty.MEDIUM,
        evidence_ids=ids,
    )


def make_service(
    monkeypatch: pytest.MonkeyPatch,
    provider: ScriptedProvider,
    search: FakeTool,
    history: FakeTool,
    **setting_overrides: object,
) -> tuple[AgentQuestionService, FakeInstallations, Repository]:
    current = repository()
    installations = FakeInstallations(current)
    service = AgentQuestionService(
        database=object(),  # type: ignore[arg-type]
        installations=installations,  # type: ignore[arg-type]
        provider=provider,
        registry=AgentToolRegistry((search, history)),
        settings=make_settings(**setting_overrides),
    )

    async def active(_repository: Repository) -> ActiveIndex:
        return ActiveIndex(1, "a" * 40, service._preprocessor.policy_fingerprint)

    monkeypatch.setattr(service, "_load_active_index", active)
    return service, installations, current


@pytest.mark.asyncio
async def test_agent_runs_code_then_history_and_orders_mixed_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ScriptedProvider(
        [
            tool_decision(AgentToolName.SEARCH_CODE),
            tool_decision(AgentToolName.GET_HISTORY),
            final_decision(["T2-H1", "T1-C1", "T1-C1"]),
        ]
    )
    service, installations, current = make_service(
        monkeypatch,
        provider,
        FakeTool(AgentToolName.SEARCH_CODE, (code_evidence(),)),
        FakeTool(AgentToolName.GET_HISTORY, (commit_evidence(),)),
    )

    result = await service.answer(
        user_id=uuid.uuid4(),
        repository_id=current.id,
        question=service.prepare_question("Show the validation code and its commit history"),
    )

    assert result.answerability is Answerability.ANSWERED
    assert result.cited_evidence_ids == ("T1-C1", "T2-H1")
    assert [step.tool for step in result.trace] == [
        AgentToolName.SEARCH_CODE,
        AgentToolName.GET_HISTORY,
    ]
    assert all(step.status is AgentStepStatus.COMPLETED for step in result.trace)
    assert installations.calls == 2


@pytest.mark.asyncio
async def test_fabricated_commit_or_pr_citation_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ScriptedProvider(
        [tool_decision(AgentToolName.GET_HISTORY), final_decision(["T1-P999"])]
    )
    service, _, current = make_service(
        monkeypatch,
        provider,
        FakeTool(AgentToolName.SEARCH_CODE),
        FakeTool(AgentToolName.GET_HISTORY, (commit_evidence("T1-H1"),)),
    )

    result = await service.answer(
        user_id=uuid.uuid4(),
        repository_id=current.id,
        question=service.prepare_question("Which pull request introduced validation?"),
    )

    assert result.answerability is Answerability.INSUFFICIENT_EVIDENCE
    assert result.cited_evidence_ids == ()
    assert result.evidence == ()


@pytest.mark.asyncio
async def test_repeated_tool_call_is_rejected_without_amplification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repeated = tool_decision(AgentToolName.SEARCH_CODE)
    provider = ScriptedProvider([repeated, repeated])
    search = FakeTool(AgentToolName.SEARCH_CODE, (code_evidence(),))
    service, _, current = make_service(
        monkeypatch,
        provider,
        search,
        FakeTool(AgentToolName.GET_HISTORY),
    )

    result = await service.answer(
        user_id=uuid.uuid4(),
        repository_id=current.id,
        question=service.prepare_question("How is validation implemented?"),
    )

    assert len(search.calls) == 1
    assert [item.status for item in result.trace] == [
        AgentStepStatus.COMPLETED,
        AgentStepStatus.REJECTED,
    ]
    assert result.trace[-1].failure_code == "repeated_tool_call"


@pytest.mark.asyncio
async def test_agent_stops_at_four_distinct_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ScriptedProvider(
        [tool_decision(AgentToolName.SEARCH_CODE, f"query {item}") for item in range(5)]
    )
    search = FakeTool(AgentToolName.SEARCH_CODE)
    service, _, current = make_service(
        monkeypatch,
        provider,
        search,
        FakeTool(AgentToolName.GET_HISTORY),
    )

    result = await service.answer(
        user_id=uuid.uuid4(),
        repository_id=current.id,
        question=service.prepare_question("How is validation implemented?"),
    )

    assert len(result.trace) == 4
    assert len(search.calls) == 4
    assert len(provider.requests) == 4
    assert result.answerability is Answerability.INSUFFICIENT_EVIDENCE


@pytest.mark.asyncio
async def test_tool_timeout_is_traced_without_leaking_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ScriptedProvider(
        [
            tool_decision(AgentToolName.GET_HISTORY, "private question"),
            final_decision([], Answerability.INSUFFICIENT_EVIDENCE),
        ]
    )
    service, _, current = make_service(
        monkeypatch,
        provider,
        FakeTool(AgentToolName.SEARCH_CODE),
        FakeTool(AgentToolName.GET_HISTORY, delay=0.03),
        agent_tool_timeout_seconds=0.01,
    )

    result = await service.answer(
        user_id=uuid.uuid4(),
        repository_id=current.id,
        question=service.prepare_question("What is the commit history?"),
    )

    assert result.trace[0].status is AgentStepStatus.TIMED_OUT
    assert result.trace[0].failure_code == "tool_timeout"
    assert "private question" not in repr(result.trace)


@pytest.mark.asyncio
async def test_partial_answer_survives_one_tool_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ScriptedProvider(
        [
            tool_decision(AgentToolName.GET_HISTORY),
            tool_decision(AgentToolName.SEARCH_CODE),
            final_decision(["T2-C1"], Answerability.PARTIALLY_ANSWERED),
        ]
    )
    service, _, current = make_service(
        monkeypatch,
        provider,
        FakeTool(AgentToolName.SEARCH_CODE, (code_evidence("T2-C1"),)),
        FakeTool(AgentToolName.GET_HISTORY, failure="github_history_unavailable"),
    )

    result = await service.answer(
        user_id=uuid.uuid4(),
        repository_id=current.id,
        question=service.prepare_question("Show code and history for validation"),
    )

    assert result.answerability is Answerability.PARTIALLY_ANSWERED
    assert result.cited_evidence_ids == ("T2-C1",)
    assert [item.status for item in result.trace] == [
        AgentStepStatus.FAILED,
        AgentStepStatus.COMPLETED,
    ]


@pytest.mark.asyncio
async def test_provider_timeout_returns_safe_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ScriptedProvider([tool_decision(AgentToolName.SEARCH_CODE)], delay=0.03)
    service, _, current = make_service(
        monkeypatch,
        provider,
        FakeTool(AgentToolName.SEARCH_CODE),
        FakeTool(AgentToolName.GET_HISTORY),
        agent_provider_timeout_seconds=0.01,
    )

    result = await service.answer(
        user_id=uuid.uuid4(),
        repository_id=current.id,
        question=service.prepare_question("How is validation implemented?"),
    )

    assert result.answerability is Answerability.TEMPORARILY_UNAVAILABLE
    assert result.cited_evidence_ids == ()


@pytest.mark.asyncio
async def test_cancellation_propagates_and_does_not_return_a_false_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ScriptedProvider([tool_decision(AgentToolName.SEARCH_CODE)], delay=1)
    service, _, current = make_service(
        monkeypatch,
        provider,
        FakeTool(AgentToolName.SEARCH_CODE),
        FakeTool(AgentToolName.GET_HISTORY),
    )
    task = asyncio.create_task(
        service.answer(
            user_id=uuid.uuid4(),
            repository_id=current.id,
            question=service.prepare_question("How is validation implemented?"),
        )
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cancellation_stops_an_active_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ScriptedProvider([tool_decision(AgentToolName.SEARCH_CODE)])
    search = CancellableTool(AgentToolName.SEARCH_CODE)
    service, _, current = make_service(
        monkeypatch,
        provider,
        search,
        FakeTool(AgentToolName.GET_HISTORY),
    )
    task = asyncio.create_task(
        service.answer(
            user_id=uuid.uuid4(),
            repository_id=current.id,
            question=service.prepare_question("How is validation implemented?"),
        )
    )
    await search.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert search.cancelled is True


@pytest.mark.asyncio
async def test_total_timeout_bounds_multiple_slow_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ScriptedProvider(
        [tool_decision(AgentToolName.SEARCH_CODE, f"query {item}") for item in range(4)]
    )
    service, _, current = make_service(
        monkeypatch,
        provider,
        FakeTool(AgentToolName.SEARCH_CODE, delay=0.02),
        FakeTool(AgentToolName.GET_HISTORY),
        agent_tool_timeout_seconds=0.03,
        agent_provider_timeout_seconds=0.02,
        agent_total_timeout_seconds=0.03,
    )
    started = time.monotonic()

    result = await service.answer(
        user_id=uuid.uuid4(),
        repository_id=current.id,
        question=service.prepare_question("How is validation implemented?"),
    )

    assert time.monotonic() - started < 0.2
    assert result.answerability is Answerability.TEMPORARILY_UNAVAILABLE


@pytest.mark.asyncio
async def test_milestone_8_caller_question_skips_provider_and_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ScriptedProvider([tool_decision(AgentToolName.SEARCH_CODE)])
    search = FakeTool(AgentToolName.SEARCH_CODE)
    service, _, current = make_service(
        monkeypatch,
        provider,
        search,
        FakeTool(AgentToolName.GET_HISTORY),
    )

    result = await service.answer(
        user_id=uuid.uuid4(),
        repository_id=current.id,
        question=service.prepare_question("Find all callers of validate"),
    )

    assert result.answerability is Answerability.UNSUPPORTED_QUESTION
    assert provider.requests == []
    assert search.calls == []


@pytest.mark.asyncio
async def test_zero_tool_final_response_is_allowed_only_as_a_nonanswer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ScriptedProvider([final_decision([], Answerability.INSUFFICIENT_EVIDENCE)])
    service, installations, current = make_service(
        monkeypatch,
        provider,
        FakeTool(AgentToolName.SEARCH_CODE),
        FakeTool(AgentToolName.GET_HISTORY),
    )

    result = await service.answer(
        user_id=uuid.uuid4(),
        repository_id=current.id,
        question=service.prepare_question("Where is definitely_missing?"),
    )

    assert result.answerability is Answerability.INSUFFICIENT_EVIDENCE
    assert result.trace == ()
    assert installations.calls == 2


@pytest.mark.asyncio
async def test_access_revocation_after_tool_execution_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ScriptedProvider(
        [tool_decision(AgentToolName.SEARCH_CODE), final_decision(["T1-C1"])]
    )
    service, installations, current = make_service(
        monkeypatch,
        provider,
        FakeTool(AgentToolName.SEARCH_CODE, (code_evidence(),)),
        FakeTool(AgentToolName.GET_HISTORY),
    )
    installations.revoke_after = 1

    with pytest.raises(InstallationAccessError):
        await service.answer(
            user_id=uuid.uuid4(),
            repository_id=current.id,
            question=service.prepare_question("How is validation implemented?"),
        )


@pytest.mark.asyncio
async def test_oversized_tool_evidence_is_rejected_before_provider_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oversized = code_evidence()
    oversized = Evidence(
        evidence_id=oversized.evidence_id,
        score=oversized.score,
        file_path=oversized.file_path,
        language=oversized.language,
        chunk_type=oversized.chunk_type,
        symbol_name=oversized.symbol_name,
        qualified_symbol_name=oversized.qualified_symbol_name,
        start_line=oversized.start_line,
        end_line=oversized.end_line,
        stable_chunk_hash=oversized.stable_chunk_hash,
        content="x" * 2_000,
    )
    provider = ScriptedProvider(
        [
            tool_decision(AgentToolName.SEARCH_CODE),
            final_decision([], Answerability.INSUFFICIENT_EVIDENCE),
        ]
    )
    service, _, current = make_service(
        monkeypatch,
        provider,
        FakeTool(AgentToolName.SEARCH_CODE, (oversized,)),
        FakeTool(AgentToolName.GET_HISTORY),
        agent_max_tool_result_bytes=1024,
    )

    result = await service.answer(
        user_id=uuid.uuid4(),
        repository_id=current.id,
        question=service.prepare_question("How is validation implemented?"),
    )

    assert result.trace[0].status is AgentStepStatus.FAILED
    assert result.trace[0].failure_code == "tool_result_too_large"
    assert result.evidence == ()
