"""Bounded direct-agent loop over server-owned repository scope and evidence."""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass

import structlog
from sqlalchemy import select

from app.agent.models import (
    AgentEvidence,
    AgentProviderProtocol,
    AgentRunResult,
    AgentStepStatus,
    AgentToolName,
    AgentTraceStep,
)
from app.agent.prompt import AgentPromptBuilder
from app.agent.tools import (
    ActiveRepositoryContext,
    AgentToolError,
    AgentToolRegistry,
    safe_argument_fingerprint,
)
from app.core.config import Settings
from app.db.models.enums import IndexBuildState, RepositoryIndexingStatus
from app.db.models.repository import Repository
from app.db.models.repository_index_build import RepositoryIndexBuild
from app.db.session import Database
from app.embeddings.preprocessing import EmbeddingPreprocessor
from app.indexing.failures import IndexingError
from app.llm.client import LLMProviderError
from app.rag.models import Answerability, AnswerUncertainty, NormalizedQuestion
from app.rag.query import QuestionPreprocessor
from app.services.installations import InstallationService

logger = structlog.get_logger(__name__)

_INSUFFICIENT = "The authorized evidence does not contain enough support to answer this question."
_UNSUPPORTED = "This question requires capabilities outside the current repository agent."
_UNAVAILABLE = "Repository analysis is temporarily unavailable. Please try again."


@dataclass(frozen=True, slots=True)
class ActiveIndex:
    index_version: int
    commit_sha: str
    preprocessing_fingerprint: str


class AgentQuestionService:
    def __init__(
        self,
        *,
        database: Database,
        installations: InstallationService,
        provider: AgentProviderProtocol,
        registry: AgentToolRegistry,
        settings: Settings,
    ) -> None:
        self._database = database
        self._installations = installations
        self._provider = provider
        self._registry = registry
        self._settings = settings
        self._questions = QuestionPreprocessor(settings)
        self._preprocessor = EmbeddingPreprocessor(settings)
        self._prompt = AgentPromptBuilder()

    def prepare_question(self, raw: str) -> NormalizedQuestion:
        return self._questions.prepare(raw)

    async def answer(
        self,
        *,
        user_id: uuid.UUID,
        repository_id: uuid.UUID,
        question: NormalizedQuestion,
    ) -> AgentRunResult:
        started = time.monotonic()
        try:
            async with asyncio.timeout(self._settings.agent_total_timeout_seconds):
                return await self._run(
                    user_id=user_id,
                    repository_id=repository_id,
                    question=question,
                    started=started,
                )
        except TimeoutError:
            logger.warning("repository_agent_timed_out", repository_id=str(repository_id))
            return self._no_answer(
                repository_id=repository_id,
                state=Answerability.TEMPORARILY_UNAVAILABLE,
                started=started,
            )

    async def _run(  # noqa: PLR0915 -- bounded loop keeps trace state in one place
        self,
        *,
        user_id: uuid.UUID,
        repository_id: uuid.UUID,
        question: NormalizedQuestion,
        started: float,
    ) -> AgentRunResult:
        repository = await self._installations.get_authorized_repository(
            user_id=user_id,
            repository_id=repository_id,
        )
        active = await self._load_active_index(repository)
        if active is None:
            return self._no_answer(
                repository_id=repository_id,
                state=Answerability.INSUFFICIENT_EVIDENCE,
                started=started,
                repository=repository,
            )
        if self._questions.is_unsupported(question):
            return self._no_answer(
                repository_id=repository_id,
                state=Answerability.UNSUPPORTED_QUESTION,
                started=started,
                active=active,
            )
        context = ActiveRepositoryContext(
            user_id=user_id,
            repository=repository,
            index_version=active.index_version,
            commit_sha=active.commit_sha,
            preprocessing_fingerprint=active.preprocessing_fingerprint,
            original_question=question.text,
        )
        evidence: list[AgentEvidence] = []
        trace: list[AgentTraceStep] = []
        completed: list[AgentToolName] = []
        failed: list[AgentToolName] = []
        failure_codes: list[str] = []
        fingerprints: set[str] = set()
        total_evidence_bytes = 0

        while len(trace) < self._settings.agent_max_tool_calls:
            try:
                async with asyncio.timeout(self._settings.agent_provider_timeout_seconds):
                    decision = await self._provider.decide(
                        self._prompt.build(
                            question=question,
                            evidence=evidence,
                            completed_tools=completed,
                            failed_tools=failed,
                            failed_tool_codes=failure_codes,
                            remaining_calls=self._settings.agent_max_tool_calls - len(trace),
                        )
                    )
            except (LLMProviderError, TimeoutError) as error:
                code = error.code if isinstance(error, LLMProviderError) else "provider_timeout"
                logger.warning(
                    "repository_agent_provider_failed",
                    repository_id=str(repository_id),
                    error_code=code,
                )
                return self._no_answer(
                    repository_id=repository_id,
                    state=Answerability.TEMPORARILY_UNAVAILABLE,
                    started=started,
                    active=active,
                    evidence_count=len(evidence),
                    trace=trace,
                )
            if decision.action == "final":
                return await self._finalize(
                    user_id=user_id,
                    repository_id=repository_id,
                    repository=repository,
                    active=active,
                    decision_answer=decision.answer or _INSUFFICIENT,
                    state=decision.answerability or Answerability.INSUFFICIENT_EVIDENCE,
                    uncertainty=decision.uncertainty or AnswerUncertainty.HIGH,
                    cited_ids=decision.evidence_ids,
                    evidence=evidence,
                    trace=trace,
                    started=started,
                )
            tool_name = decision.tool_name
            if tool_name is None:
                return self._no_answer(
                    repository_id=repository_id,
                    state=Answerability.TEMPORARILY_UNAVAILABLE,
                    started=started,
                    active=active,
                    evidence_count=len(evidence),
                    trace=trace,
                )
            step = len(trace) + 1
            arguments = (
                decision.arguments.model_dump(exclude_none=True)
                if decision.arguments is not None
                else {}
            )
            fingerprint = safe_argument_fingerprint(tool_name, arguments)
            if fingerprint in fingerprints:
                trace.append(
                    AgentTraceStep(
                        step=step,
                        tool=tool_name,
                        argument_fingerprint=fingerprint,
                        status=AgentStepStatus.REJECTED,
                        duration_ms=0,
                        result_count=0,
                        failure_code="repeated_tool_call",
                        contributed_evidence=False,
                    )
                )
                break
            fingerprints.add(fingerprint)
            tool_started = time.monotonic()
            try:
                async with asyncio.timeout(self._settings.agent_tool_timeout_seconds):
                    result = await self._registry.get(tool_name).execute(
                        context,
                        arguments,
                        step=step,
                    )
                encoded_size = len(
                    json.dumps(
                        [self._prompt.serialize_evidence(item) for item in result],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode()
                )
                self._validate_evidence_size(encoded_size, total_evidence_bytes)
            except TimeoutError:
                failed.append(tool_name)
                failure_codes.append("tool_timeout")
                trace.append(
                    self._trace_step(
                        step,
                        tool_name,
                        fingerprint,
                        AgentStepStatus.TIMED_OUT,
                        tool_started,
                        failure_code="tool_timeout",
                    )
                )
                continue
            except (AgentToolError, IndexingError) as error:
                failed.append(tool_name)
                failure_code = error.code
                failure_codes.append(failure_code)
                trace.append(
                    self._trace_step(
                        step,
                        tool_name,
                        fingerprint,
                        AgentStepStatus.FAILED,
                        tool_started,
                        failure_code=failure_code,
                    )
                )
                continue
            evidence.extend(result)
            total_evidence_bytes += encoded_size
            completed.append(tool_name)
            trace.append(
                self._trace_step(
                    step,
                    tool_name,
                    fingerprint,
                    AgentStepStatus.COMPLETED,
                    tool_started,
                    result_count=len(result),
                    contributed=bool(result),
                )
            )
        unavailable_codes = {
            "tool_timeout",
            "call_graph_unavailable",
            "caller_query_unavailable",
            "caller_scope_changed",
            "caller_scope_revoked",
        }
        return self._no_answer(
            repository_id=repository_id,
            state=(
                Answerability.TEMPORARILY_UNAVAILABLE
                if unavailable_codes.intersection(failure_codes)
                else Answerability.INSUFFICIENT_EVIDENCE
            ),
            started=started,
            active=active,
            evidence_count=len(evidence),
            trace=trace,
        )

    async def _finalize(
        self,
        *,
        user_id: uuid.UUID,
        repository_id: uuid.UUID,
        repository: Repository,
        active: ActiveIndex,
        decision_answer: str,
        state: Answerability,
        uncertainty: AnswerUncertainty,
        cited_ids: list[str],
        evidence: list[AgentEvidence],
        trace: list[AgentTraceStep],
        started: float,
    ) -> AgentRunResult:
        final_repository = await self._installations.get_authorized_repository(
            user_id=user_id,
            repository_id=repository_id,
        )
        final_active = await self._load_active_index(final_repository)
        if final_repository.id != repository.id or final_active != active:
            return self._no_answer(
                repository_id=repository_id,
                state=Answerability.TEMPORARILY_UNAVAILABLE,
                started=started,
                repository=final_repository,
                trace=trace,
            )
        by_id = {item.evidence_id: item for item in evidence}
        unique_ids = tuple(dict.fromkeys(cited_ids))
        if state in {Answerability.ANSWERED, Answerability.PARTIALLY_ANSWERED}:
            if not unique_ids or any(item not in by_id for item in unique_ids):
                return self._no_answer(
                    repository_id=repository_id,
                    state=Answerability.INSUFFICIENT_EVIDENCE,
                    started=started,
                    active=active,
                    evidence_count=len(evidence),
                    trace=trace,
                )
            ordered_ids = tuple(
                item.evidence_id for item in evidence if item.evidence_id in unique_ids
            )
        else:
            ordered_ids = ()
        return AgentRunResult(
            repository_id=repository_id,
            answer=decision_answer,
            answerability=state,
            uncertainty=uncertainty,
            evidence=tuple(evidence),
            cited_evidence_ids=ordered_ids,
            commit_sha=active.commit_sha,
            index_version=active.index_version,
            retrieved_evidence_count=len(evidence),
            trace=tuple(trace),
            duration_ms=self._elapsed_ms(started),
        )

    async def _load_active_index(self, repository: Repository) -> ActiveIndex | None:
        if (
            repository.indexing_status is not RepositoryIndexingStatus.COMPLETE
            or repository.index_version < 1
            or repository.last_indexed_commit_sha is None
            or repository.active_vector_count < 1
        ):
            return None
        async with self._database.session() as session:
            build = await session.scalar(
                select(RepositoryIndexBuild).where(
                    RepositoryIndexBuild.repository_id == repository.id,
                    RepositoryIndexBuild.index_version == repository.index_version,
                    RepositoryIndexBuild.state == IndexBuildState.ACTIVE,
                    RepositoryIndexBuild.commit_sha == repository.last_indexed_commit_sha,
                    RepositoryIndexBuild.embedding_model_identifier
                    == self._settings.embedding_model_identifier,
                    RepositoryIndexBuild.embedding_model_revision
                    == self._settings.embedding_model_revision,
                    RepositoryIndexBuild.embedding_dimension == self._settings.embedding_dimension,
                    RepositoryIndexBuild.preprocessing_fingerprint
                    == self._preprocessor.policy_fingerprint,
                    RepositoryIndexBuild.vector_count == repository.active_vector_count,
                )
            )
        if build is None:
            return None
        return ActiveIndex(build.index_version, build.commit_sha, build.preprocessing_fingerprint)

    def _validate_evidence_size(self, encoded_size: int, total_evidence_bytes: int) -> None:
        if encoded_size > self._settings.agent_max_tool_result_bytes:
            raise AgentToolError("tool_result_too_large")
        if total_evidence_bytes + encoded_size > self._settings.agent_max_total_evidence_bytes:
            raise AgentToolError("total_evidence_too_large")

    @staticmethod
    def _trace_step(
        step: int,
        tool: AgentToolName,
        fingerprint: str,
        status: AgentStepStatus,
        started: float,
        *,
        result_count: int = 0,
        failure_code: str | None = None,
        contributed: bool = False,
    ) -> AgentTraceStep:
        return AgentTraceStep(
            step=step,
            tool=tool,
            argument_fingerprint=fingerprint,
            status=status,
            duration_ms=AgentQuestionService._elapsed_ms(started),
            result_count=result_count,
            failure_code=failure_code,
            contributed_evidence=contributed,
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))

    @staticmethod
    def _no_answer(
        *,
        repository_id: uuid.UUID,
        state: Answerability,
        started: float,
        active: ActiveIndex | None = None,
        repository: Repository | None = None,
        evidence_count: int = 0,
        trace: list[AgentTraceStep] | None = None,
    ) -> AgentRunResult:
        answer = (
            _UNSUPPORTED
            if state is Answerability.UNSUPPORTED_QUESTION
            else _UNAVAILABLE
            if state is Answerability.TEMPORARILY_UNAVAILABLE
            else _INSUFFICIENT
        )
        return AgentRunResult(
            repository_id=repository_id,
            answer=answer,
            answerability=state,
            uncertainty=(
                AnswerUncertainty.HIGH
                if state is Answerability.INSUFFICIENT_EVIDENCE
                else AnswerUncertainty.NOT_APPLICABLE
            ),
            evidence=(),
            cited_evidence_ids=(),
            commit_sha=(
                active.commit_sha
                if active is not None
                else repository.last_indexed_commit_sha
                if repository is not None
                else None
            ),
            index_version=(
                active.index_version
                if active is not None
                else repository.index_version
                if repository is not None
                else 0
            ),
            retrieved_evidence_count=evidence_count,
            trace=tuple(trace or ()),
            duration_ms=AgentQuestionService._elapsed_ms(started),
        )
