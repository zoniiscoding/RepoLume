"""Trusted, bounded tools available to the direct repository agent."""

import hashlib
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from pydantic import ValidationError

from app.agent.models import (
    AgentEvidence,
    AgentToolName,
    CommitEvidence,
    GetHistoryArguments,
    PullRequestEvidence,
    SearchCodeArguments,
)
from app.core.config import Settings
from app.db.models.repository import Repository
from app.embeddings.client import EmbeddingProviderProtocol
from app.embeddings.preprocessing import EmbeddingPreprocessor
from app.github.client import GitHubAPIError, GitHubHistoryClientProtocol
from app.github.schemas import GitHubHistoryBundle
from app.rag.evidence import EvidenceSelector
from app.services.installations import InstallationService
from app.vector.qdrant import VectorScope, VectorStoreProtocol, embedding_model_fingerprint

_WORD_PATTERN = re.compile(r"[A-Za-z0-9_./-]+")
_COMMIT_SHA_PATTERN = re.compile(r"(?<![0-9a-f])([0-9a-f]{7,40})(?![0-9a-f])", re.I)
_MINIMUM_HISTORY_TERM_LENGTH = 3


class AgentToolError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ActiveRepositoryContext:
    user_id: uuid.UUID
    repository: Repository
    index_version: int
    commit_sha: str
    preprocessing_fingerprint: str
    original_question: str


class AgentToolProtocol(Protocol):
    name: AgentToolName

    async def execute(
        self,
        context: ActiveRepositoryContext,
        arguments: Mapping[str, str],
        *,
        step: int,
    ) -> tuple[AgentEvidence, ...]: ...


class SearchCodeTool:
    name = AgentToolName.SEARCH_CODE

    def __init__(
        self,
        *,
        embeddings: EmbeddingProviderProtocol,
        vectors: VectorStoreProtocol,
        settings: Settings,
    ) -> None:
        self._embeddings = embeddings
        self._vectors = vectors
        self._settings = settings
        self._preprocessor = EmbeddingPreprocessor(settings)
        self._selector = EvidenceSelector(settings)

    async def execute(
        self,
        context: ActiveRepositoryContext,
        arguments: Mapping[str, str],
        *,
        step: int,
    ) -> tuple[AgentEvidence, ...]:
        try:
            parsed = SearchCodeArguments.model_validate(arguments)
            prepared = self._preprocessor.prepare_query(parsed.query)
            query_vector = await self._embeddings.embed_query(prepared)
            hits = await self._vectors.search(
                VectorScope(
                    context.repository.installation_id,
                    context.repository.id,
                    context.index_version,
                ),
                query_vector=query_vector,
                commit_sha=context.commit_sha,
                model_fingerprint=embedding_model_fingerprint(
                    self._settings, context.preprocessing_fingerprint
                ),
                preprocessing_fingerprint=context.preprocessing_fingerprint,
                limit=self._settings.rag_retrieval_overfetch,
                score_threshold=self._settings.rag_retrieval_score_threshold,
            )
        except (ValidationError, ValueError) as error:
            raise AgentToolError("invalid_search_code_arguments") from error
        selected = self._selector.select(hits)
        return tuple(
            replace(item, evidence_id=f"T{step}-C{position}")
            for position, item in enumerate(selected, start=1)
        )


class GetHistoryTool:
    name = AgentToolName.GET_HISTORY

    def __init__(
        self,
        *,
        installations: InstallationService,
        github: GitHubHistoryClientProtocol,
        settings: Settings,
    ) -> None:
        self._installations = installations
        self._github = github
        self._limit = settings.agent_history_commit_limit
        self._message_bytes = settings.agent_history_max_message_bytes
        self._patch_bytes = settings.agent_history_max_patch_bytes
        self._max_paths = settings.agent_history_max_paths

    async def execute(
        self,
        context: ActiveRepositoryContext,
        arguments: Mapping[str, str],
        *,
        step: int,
    ) -> tuple[AgentEvidence, ...]:
        try:
            GetHistoryArguments.model_validate(arguments)
        except ValidationError as error:
            raise AgentToolError("invalid_get_history_arguments") from error
        installation = await self._installations.get_authorized_installation(
            user_id=context.user_id,
            installation_id=context.repository.installation_id,
        )
        try:
            token = await self._github.create_repository_installation_token(
                installation.github_installation_id,
                repository_id=context.repository.github_repository_id,
            )
            requested_sha = _COMMIT_SHA_PATTERN.search(context.original_question)
            bundles = await self._github.get_repository_history(
                token,
                owner=context.repository.github_owner,
                repository=context.repository.github_name,
                revision=requested_sha.group(1).casefold() if requested_sha else context.commit_sha,
                limit=self._limit,
            )
        except GitHubAPIError as error:
            raise AgentToolError("github_history_unavailable") from error
        ranked = self._rank(context.original_question, bundles)
        result: list[AgentEvidence] = []
        for position, bundle in enumerate(ranked, start=1):
            commit = bundle.commit
            files = tuple(item.filename for item in commit.files[: self._max_paths])
            patches = "\n".join(item.patch or "" for item in commit.files if item.patch)
            committed_at = (
                commit.commit.committer.date
                if commit.commit.committer is not None
                else commit.commit.author.date
                if commit.commit.author is not None
                else None
            )
            if committed_at is None:
                continue
            result.append(
                CommitEvidence(
                    evidence_id=f"T{step}-H{position}",
                    commit_sha=commit.sha,
                    message=self._truncate(commit.commit.message, self._message_bytes),
                    committed_at=committed_at,
                    author_login=commit.author.login if commit.author else None,
                    parent_shas=tuple(item.sha for item in commit.parents),
                    changed_paths=files,
                    patch_excerpt=self._truncate(patches, self._patch_bytes) if patches else None,
                    html_url=commit.html_url,
                )
            )
            for pull_position, pull in enumerate(bundle.pull_requests, start=1):
                result.append(
                    PullRequestEvidence(
                        evidence_id=f"T{step}-P{position}-{pull_position}",
                        number=pull.number,
                        title=self._truncate(pull.title, self._message_bytes),
                        state=pull.state,
                        author_login=pull.user.login if pull.user else None,
                        merged_at=pull.merged_at,
                        merge_commit_sha=pull.merge_commit_sha,
                        changed_paths=files,
                        body_excerpt=(
                            self._truncate(pull.body, self._message_bytes) if pull.body else None
                        ),
                        html_url=pull.html_url,
                    )
                )
        return tuple(result)

    @staticmethod
    def _rank(query: str, bundles: Sequence[GitHubHistoryBundle]) -> Sequence[GitHubHistoryBundle]:
        terms = {
            item.casefold()
            for item in _WORD_PATTERN.findall(query)
            if len(item) >= _MINIMUM_HISTORY_TERM_LENGTH
        }

        def score(bundle: GitHubHistoryBundle) -> int:
            commit = bundle.commit
            haystack = " ".join(
                (
                    commit.commit.message,
                    *(item.filename for item in commit.files),
                    *(item.patch or "" for item in commit.files),
                )
            ).casefold()
            return sum(term in haystack for term in terms)

        return tuple(sorted(bundles, key=score, reverse=True))

    @staticmethod
    def _truncate(value: str, maximum_bytes: int) -> str:
        encoded = value.encode("utf-8")
        if len(encoded) <= maximum_bytes:
            return value
        return encoded[:maximum_bytes].decode("utf-8", errors="ignore")


class AgentToolRegistry:
    """Immutable allowlist; there is no dynamic import, shell, or arbitrary network tool."""

    def __init__(self, tools: Sequence[AgentToolProtocol]) -> None:
        self._tools = {item.name: item for item in tools}
        if set(self._tools) != set(AgentToolName):
            raise ValueError("agent_tool_registry_mismatch")

    def get(self, name: AgentToolName) -> AgentToolProtocol:
        return self._tools[name]

    @property
    def names(self) -> tuple[AgentToolName, ...]:
        return tuple(sorted(self._tools, key=str))


def safe_argument_fingerprint(name: AgentToolName, arguments: Mapping[str, str]) -> str:
    canonical = "\x1f".join((name.value, *(f"{key}={arguments[key]}" for key in sorted(arguments))))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
