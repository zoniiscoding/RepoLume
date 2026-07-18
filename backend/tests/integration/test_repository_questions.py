"""PostgreSQL-backed grounded question authorization and active-index checks."""

import asyncio
import os
import uuid
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.application import create_app
from app.auth.tokens import TokenService
from app.db.models.call_edge import CallEdge
from app.db.models.enums import (
    Confidence,
    GitHubAccountType,
    IndexBuildState,
    IndexCleanupStatus,
    InstallationMemberRole,
    InstallationStatus,
    RepositoryIndexingStatus,
    RepositorySelection,
    ResolutionType,
    SymbolType,
)
from app.db.models.github_installation import GitHubInstallation, InstallationMember
from app.db.models.repository import Repository
from app.db.models.repository_index_build import RepositoryIndexBuild
from app.db.models.symbol_definition import SymbolDefinition
from app.db.models.user import User
from app.db.session import Database
from app.embeddings.preprocessing import EmbeddingPreprocessor, PreparedEmbedding
from app.github.schemas import GitHubHistoryBundle
from app.llm.client import (
    DraftAnswerability,
    DraftUncertainty,
    GroundedAnswerDraft,
    GroundedGenerationRequest,
)
from app.vector.qdrant import RetrievalHit, VectorScope
from tests.conftest import make_settings

pytestmark = pytest.mark.integration


def database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if value is None:
        pytest.fail("TEST_DATABASE_URL must target a disposable PostgreSQL database")
    return value


async def reset_database() -> None:
    engine = create_async_engine(database_url())
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE call_edges, chat_messages, chat_sessions, indexing_jobs, "
                "oauth_states, refresh_tokens, repositories, symbol_definitions, usage_records, "
                "installation_members, webhook_deliveries, github_installations, users CASCADE"
            )
        )
    await engine.dispose()


class FakeGitHub:
    def __init__(self) -> None:
        self.repository_token_requests: list[tuple[int, int]] = []
        self.history_requests: list[dict[str, object]] = []

    async def create_repository_installation_token(
        self, installation_id: int, *, repository_id: int
    ) -> SecretStr:
        self.repository_token_requests.append((installation_id, repository_id))
        return SecretStr("ephemeral-history-token")

    async def get_repository_history(
        self, token: SecretStr, **kwargs: object
    ) -> tuple[GitHubHistoryBundle, ...]:
        assert token.get_secret_value() == "ephemeral-history-token"
        self.history_requests.append(dict(kwargs))
        return (
            GitHubHistoryBundle.model_validate(
                {
                    "commit": {
                        "sha": "d" * 40,
                        "html_url": (f"https://github.com/owner/private-repo/commit/{'d' * 40}"),
                        "commit": {
                            "message": "Add validation",
                            "author": {
                                "name": "Developer",
                                "date": "2026-01-01T00:00:00Z",
                            },
                            "committer": {
                                "name": "Developer",
                                "date": "2026-01-01T00:00:00Z",
                            },
                        },
                        "parents": [{"sha": "c" * 40}],
                        "files": [
                            {
                                "filename": "app/service.py",
                                "status": "modified",
                                "patch": "+def validate(response)",
                            }
                        ],
                    },
                    "pull_requests": [
                        {
                            "number": 42,
                            "title": "Add validation",
                            "state": "closed",
                            "html_url": "https://github.com/owner/private-repo/pull/42",
                            "user": {"login": "developer"},
                            "merged_at": "2026-01-02T00:00:00Z",
                            "merge_commit_sha": "e" * 40,
                        }
                    ],
                }
            ),
        )

    async def close(self) -> None:
        return None


class FakeEmbeddings:
    def __init__(self) -> None:
        self.queries: list[PreparedEmbedding] = []

    async def is_ready(self) -> bool:
        return True

    async def embed_documents(
        self, documents: list[PreparedEmbedding]
    ) -> dict[str, tuple[float, ...]]:
        del documents
        return {}

    async def embed_query(self, query: PreparedEmbedding) -> tuple[float, ...]:
        self.queries.append(query)
        return (1.0,) + (0.0,) * 767

    async def close(self) -> None:
        return None


class FakeVectors:
    def __init__(self, *, return_evidence: bool = True) -> None:
        self.scopes: list[VectorScope] = []
        self.return_evidence = return_evidence

    async def is_ready(self) -> bool:
        return True

    async def search(self, scope: VectorScope, **kwargs: object) -> tuple[RetrievalHit, ...]:
        assert kwargs["commit_sha"] == "a" * 40
        assert kwargs["limit"] == 12
        self.scopes.append(scope)
        if not self.return_evidence:
            return ()
        return (
            RetrievalHit(
                score=0.91,
                file_path="app/service.py",
                language="python",
                chunk_type="function",
                symbol_name="validate",
                qualified_symbol_name="app.service.validate",
                start_line=10,
                end_line=20,
                content="def validate(response):\n    return response.is_valid",
                stable_chunk_hash="b" * 64,
            ),
        )

    async def close(self) -> None:
        return None


class FakeLLM:
    def __init__(self, *, evidence_ids: list[str] | None = None) -> None:
        self.requests: list[GroundedGenerationRequest] = []
        self.evidence_ids = ["E1"] if evidence_ids is None else evidence_ids

    async def generate(self, request: GroundedGenerationRequest) -> GroundedAnswerDraft:
        self.requests.append(request)
        return GroundedAnswerDraft(
            answer="The validate function returns the response validity flag.",
            answerability=DraftAnswerability.ANSWERED,
            uncertainty=DraftUncertainty.LOW,
            evidence_ids=self.evidence_ids,
        )

    async def close(self) -> None:
        return None


async def seed_active_repository() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    settings = make_settings()
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    async with database.session() as session:
        owner = User(github_user_id=101, github_login="owner")
        other = User(github_user_id=202, github_login="other")
        session.add_all((owner, other))
        await session.flush()
        installation = GitHubInstallation(
            github_installation_id=501,
            account_type=GitHubAccountType.USER,
            account_github_id=101,
            account_login="owner",
            installed_by_user_id=owner.id,
            status=InstallationStatus.ACTIVE,
            permissions_json={"contents": "read", "metadata": "read"},
            repository_selection=RepositorySelection.SELECTED,
        )
        session.add(installation)
        await session.flush()
        session.add(
            InstallationMember(
                installation_id=installation.id,
                user_id=owner.id,
                role=InstallationMemberRole.OWNER,
                verified_at=datetime.now(UTC),
            )
        )
        repository = Repository(
            installation_id=installation.id,
            github_repository_id=9001,
            github_owner="owner",
            github_name="private-repo",
            github_full_name="owner/private-repo",
            github_url="https://github.com/owner/private-repo",
            is_private=True,
            default_branch="main",
            last_indexed_commit_sha="a" * 40,
            index_version=1,
            indexing_status=RepositoryIndexingStatus.COMPLETE,
            indexing_progress=100,
            indexing_stage="complete",
            active_vector_count=1,
        )
        session.add(repository)
        await session.flush()
        session.add(
            RepositoryIndexBuild(
                repository_id=repository.id,
                index_version=1,
                state=IndexBuildState.ACTIVE,
                cleanup_status=IndexCleanupStatus.NOT_REQUIRED,
                commit_sha="a" * 40,
                embedding_model_identifier=settings.embedding_model_identifier,
                embedding_model_revision=settings.embedding_model_revision,
                embedding_dimension=settings.embedding_dimension,
                preprocessing_fingerprint=EmbeddingPreprocessor(settings).policy_fingerprint,
                expected_chunk_count=1,
                embedded_chunk_count=1,
                vector_count=1,
                failed_chunk_count=0,
                skipped_chunk_count=0,
                call_site_count=1,
                exact_edge_count=1,
                ambiguous_edge_count=0,
                unresolved_call_count=0,
                graph_warning_count=0,
                graph_fingerprint="f" * 64,
                graph_validated=True,
                activated_at=datetime.now(UTC),
            )
        )
        target = SymbolDefinition(
            repository_id=repository.id,
            index_version=1,
            file_path="app/service.py",
            language="python",
            symbol_name="validate",
            qualified_name="app.service.validate",
            symbol_type=SymbolType.FUNCTION,
            start_line=10,
            end_line=20,
            content_hash="1" * 64,
            commit_sha="a" * 40,
        )
        caller = SymbolDefinition(
            repository_id=repository.id,
            index_version=1,
            file_path="app/api.py",
            language="python",
            symbol_name="handle_request",
            qualified_name="app.api.handle_request",
            symbol_type=SymbolType.FUNCTION,
            start_line=30,
            end_line=40,
            content_hash="2" * 64,
            commit_sha="a" * 40,
        )
        session.add_all((target, caller))
        await session.flush()
        session.add(
            CallEdge(
                repository_id=repository.id,
                index_version=1,
                caller_symbol_id=caller.id,
                callee_symbol_id=target.id,
                unresolved_callee_name=None,
                file_path=caller.file_path,
                call_line=35,
                call_end_line=35,
                call_expression="validate",
                call_site_fingerprint="3" * 64,
                resolution_type=ResolutionType.EXACT_DIRECT_IMPORT,
                confidence=Confidence.HIGH,
                commit_sha="a" * 40,
            )
        )
        await session.commit()
    result = (owner.id, other.id, repository.id)
    await database.dispose()
    return result


async def mutate_repository_state(repository_id: uuid.UUID, state: str) -> None:
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    async with database.session() as session:
        repository = await session.get(Repository, repository_id)
        assert repository is not None
        installation = await session.get(GitHubInstallation, repository.installation_id)
        assert installation is not None
        build = await session.scalar(
            select(RepositoryIndexBuild).where(RepositoryIndexBuild.repository_id == repository_id)
        )
        assert build is not None
        if state == "suspended_installation":
            installation.status = InstallationStatus.SUSPENDED
            installation.suspended_at = datetime.now(UTC)
        elif state == "deleted_installation":
            installation.status = InstallationStatus.DELETED
            installation.deleted_at = datetime.now(UTC)
        elif state == "deleted_repository":
            repository.deleted_at = datetime.now(UTC)
        elif state == "not_indexed":
            repository.indexing_status = RepositoryIndexingStatus.NOT_INDEXED
        elif state == "replacement_building":
            repository.indexing_status = RepositoryIndexingStatus.EMBEDDING
            repository.indexing_stage = "embedding"
        elif state == "incomplete_build":
            build.state = IndexBuildState.READY
        elif state == "stale_version":
            repository.index_version = 2
        elif state == "incompatible_model":
            build.embedding_model_revision = "different"
        else:
            raise AssertionError("unknown_test_state")
        await session.commit()
    await database.dispose()


async def add_ambiguous_target(repository_id: uuid.UUID) -> None:
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    async with database.session() as session:
        session.add(
            SymbolDefinition(
                repository_id=repository_id,
                index_version=1,
                file_path="other/service.py",
                language="python",
                symbol_name="validate",
                qualified_name="other.service.validate",
                symbol_type=SymbolType.FUNCTION,
                start_line=1,
                end_line=2,
                content_hash="4" * 64,
                commit_sha="a" * 40,
            )
        )
        await session.commit()
    await database.dispose()


async def invalidate_graph(repository_id: uuid.UUID) -> None:
    engine = create_async_engine(database_url())
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE repository_index_builds SET graph_validated = false "
                "WHERE repository_id = :repository_id"
            ),
            {"repository_id": repository_id},
        )
    await engine.dispose()


async def add_scoped_graph_distractors(repository_id: uuid.UUID) -> None:
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    async with database.session() as session:
        repository = await session.get(Repository, repository_id)
        assert repository is not None
        other_repository = Repository(
            installation_id=repository.installation_id,
            github_repository_id=9002,
            github_owner="owner",
            github_name="other-private-repo",
            github_full_name="owner/other-private-repo",
            github_url="https://github.com/owner/other-private-repo",
            is_private=True,
            default_branch="main",
            last_indexed_commit_sha="b" * 40,
            index_version=1,
            indexing_status=RepositoryIndexingStatus.COMPLETE,
            indexing_progress=100,
            indexing_stage="complete",
            active_vector_count=1,
        )
        session.add(other_repository)
        await session.flush()

        symbols = (
            SymbolDefinition(
                repository_id=repository.id,
                index_version=2,
                file_path="inactive/service.py",
                language="python",
                symbol_name="validate",
                qualified_name="inactive.service.validate",
                symbol_type=SymbolType.FUNCTION,
                start_line=1,
                end_line=2,
                content_hash="5" * 64,
                commit_sha="c" * 40,
            ),
            SymbolDefinition(
                repository_id=repository.id,
                index_version=2,
                file_path="inactive/caller.py",
                language="python",
                symbol_name="stale_caller",
                qualified_name="inactive.caller.stale_caller",
                symbol_type=SymbolType.FUNCTION,
                start_line=1,
                end_line=3,
                content_hash="6" * 64,
                commit_sha="c" * 40,
            ),
            SymbolDefinition(
                repository_id=other_repository.id,
                index_version=1,
                file_path="other/service.py",
                language="python",
                symbol_name="validate",
                qualified_name="other.service.validate",
                symbol_type=SymbolType.FUNCTION,
                start_line=1,
                end_line=2,
                content_hash="7" * 64,
                commit_sha="b" * 40,
            ),
            SymbolDefinition(
                repository_id=other_repository.id,
                index_version=1,
                file_path="other/private.py",
                language="python",
                symbol_name="private_caller",
                qualified_name="other.private.private_caller",
                symbol_type=SymbolType.FUNCTION,
                start_line=1,
                end_line=3,
                content_hash="8" * 64,
                commit_sha="b" * 40,
            ),
        )
        session.add_all(symbols)
        await session.flush()
        inactive_target, inactive_caller, other_target, other_caller = symbols
        session.add_all(
            (
                CallEdge(
                    repository_id=repository.id,
                    index_version=2,
                    caller_symbol_id=inactive_caller.id,
                    callee_symbol_id=inactive_target.id,
                    unresolved_callee_name=None,
                    file_path=inactive_caller.file_path,
                    call_line=2,
                    call_end_line=2,
                    call_expression="validate",
                    call_site_fingerprint="9" * 64,
                    resolution_type=ResolutionType.EXACT_SAME_FILE,
                    confidence=Confidence.HIGH,
                    commit_sha="c" * 40,
                ),
                CallEdge(
                    repository_id=other_repository.id,
                    index_version=1,
                    caller_symbol_id=other_caller.id,
                    callee_symbol_id=other_target.id,
                    unresolved_callee_name=None,
                    file_path=other_caller.file_path,
                    call_line=2,
                    call_end_line=2,
                    call_expression="validate",
                    call_site_fingerprint="a" * 64,
                    resolution_type=ResolutionType.EXACT_SAME_FILE,
                    confidence=Confidence.HIGH,
                    commit_sha="b" * 40,
                ),
            )
        )
        await session.commit()
    await database.dispose()


@pytest.fixture(autouse=True)
def clean_database() -> Iterator[None]:
    asyncio.run(reset_database())
    yield
    asyncio.run(reset_database())


def test_repository_question_is_authorized_scoped_grounded_and_cited() -> None:
    settings = make_settings()
    owner_id, other_id, repository_id = asyncio.run(seed_active_repository())
    embeddings = FakeEmbeddings()
    vectors = FakeVectors()
    llm = FakeLLM()
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    app = create_app(
        settings=settings,
        database=database,
        github_client=FakeGitHub(),  # type: ignore[arg-type]
        vector_store=vectors,
        embedding_provider=embeddings,  # type: ignore[arg-type]
        llm_provider=llm,
    )
    tokens = TokenService(settings)
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/repositories/{repository_id}/questions",
            headers={"Authorization": f"Bearer {tokens.issue_access_token(owner_id).value}"},
            json={"question": "How does validate inspect the response?"},
        )
        denied = client.post(
            f"/api/v1/repositories/{repository_id}/questions",
            headers={"Authorization": f"Bearer {tokens.issue_access_token(other_id).value}"},
            json={"question": "How does validate inspect the response?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body.pop("duration_ms") >= 0
    trace = body.pop("trace")
    assert len(trace) == 1
    assert trace[0].pop("duration_ms") >= 0
    assert trace == [
        {
            "step": 1,
            "tool": "search_code",
            "argument_fingerprint": trace[0]["argument_fingerprint"],
            "status": "completed",
            "result_count": 1,
            "failure_code": None,
            "contributed_evidence": True,
        }
    ]
    assert body == {
        "repository_id": str(repository_id),
        "answer": "The validate function returns the response validity flag.",
        "answerability": "answered",
        "uncertainty": "low",
        "citations": [
            {
                "source_type": "code",
                "evidence_id": "T1-C1",
                "file_path": "app/service.py",
                "start_line": 10,
                "end_line": 20,
                "symbol_name": "validate",
                "qualified_symbol_name": "app.service.validate",
                "chunk_type": "function",
                "commit_sha": "a" * 40,
                "supporting_excerpt": "def validate(response):\n    return response.is_valid",
            }
        ],
        "indexed_commit_sha": "a" * 40,
        "active_index_version": 1,
        "retrieved_evidence_count": 1,
        "tool_call_count": 1,
    }
    assert vectors.scopes == [VectorScope(vectors.scopes[0].installation_id, repository_id, 1)]
    assert len(embeddings.queries) == 1
    assert len(llm.requests) == 1
    assert denied.status_code == 404
    assert "private-repo" not in denied.text


def test_unsupported_question_skips_embedding_retrieval_and_llm() -> None:
    settings = make_settings()
    owner_id, _, repository_id = asyncio.run(seed_active_repository())
    embeddings = FakeEmbeddings()
    vectors = FakeVectors()
    llm = FakeLLM()
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    app = create_app(
        settings=settings,
        database=database,
        github_client=FakeGitHub(),  # type: ignore[arg-type]
        vector_store=vectors,
        embedding_provider=embeddings,  # type: ignore[arg-type]
        llm_provider=llm,
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/repositories/{repository_id}/questions",
            headers={
                "Authorization": (
                    f"Bearer {TokenService(settings).issue_access_token(owner_id).value}"
                )
            },
            json={"question": "Who calls validate in the runtime call graph?"},
        )

    assert response.status_code == 200
    assert response.json()["answerability"] == "unsupported_question"
    assert response.json()["citations"] == []
    assert embeddings.queries == []
    assert vectors.scopes == []
    assert llm.requests == []


def test_caller_question_is_tenant_scoped_and_returns_static_graph_citation() -> None:
    settings = make_settings()
    owner_id, other_id, repository_id = asyncio.run(seed_active_repository())
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    app = create_app(
        settings=settings,
        database=database,
        github_client=FakeGitHub(),  # type: ignore[arg-type]
        vector_store=FakeVectors(),
        embedding_provider=FakeEmbeddings(),  # type: ignore[arg-type]
    )
    tokens = TokenService(settings)
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/repositories/{repository_id}/questions",
            headers={"Authorization": f"Bearer {tokens.issue_access_token(owner_id).value}"},
            json={"question": "What calls validate?"},
        )
        denied = client.post(
            f"/api/v1/repositories/{repository_id}/questions",
            headers={"Authorization": f"Bearer {tokens.issue_access_token(other_id).value}"},
            json={"question": "What calls validate?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["answerability"] == "answered"
    assert body["trace"][0]["tool"] == "find_callers"
    assert body["citations"] == [
        {
            "source_type": "caller",
            "evidence_id": "T1-G1",
            "target_symbol_name": "validate",
            "target_qualified_name": "app.service.validate",
            "target_file_path": "app/service.py",
            "caller_symbol_name": "handle_request",
            "caller_qualified_name": "app.api.handle_request",
            "caller_file_path": "app/api.py",
            "caller_start_line": 30,
            "caller_end_line": 40,
            "call_line": 35,
            "call_end_line": 35,
            "call_expression": "validate",
            "resolution_type": "exact_direct_import",
            "confidence": "high",
            "commit_sha": "a" * 40,
            "index_version": 1,
            "limitation": "Static Python analysis; runtime-dispatched calls may be absent.",
        }
    ]
    assert denied.status_code == 404
    assert "handle_request" not in denied.text


def test_caller_lookup_excludes_other_repository_and_inactive_version_edges() -> None:
    settings = make_settings()
    owner_id, _, repository_id = asyncio.run(seed_active_repository())
    asyncio.run(add_scoped_graph_distractors(repository_id))
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    app = create_app(
        settings=settings,
        database=database,
        github_client=FakeGitHub(),  # type: ignore[arg-type]
        vector_store=FakeVectors(),
        embedding_provider=FakeEmbeddings(),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/repositories/{repository_id}/questions",
            headers={
                "Authorization": (
                    f"Bearer {TokenService(settings).issue_access_token(owner_id).value}"
                )
            },
            json={"question": "What calls validate?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert [item["caller_file_path"] for item in body["citations"]] == ["app/api.py"]
    assert "inactive/caller.py" not in response.text
    assert "other/private.py" not in response.text


@pytest.mark.parametrize(
    ("question", "expected_tools", "expected_citation_types"),
    [
        (
            "Show the implementation and callers of validate",
            ["find_callers", "search_code"],
            ["caller", "code"],
        ),
        (
            "What calls validate and which commit introduced it?",
            ["find_callers", "get_history"],
            ["caller", "commit"],
        ),
    ],
)
def test_caller_questions_combine_only_the_required_bounded_tools(
    question: str,
    expected_tools: list[str],
    expected_citation_types: list[str],
) -> None:
    settings = make_settings()
    owner_id, _, repository_id = asyncio.run(seed_active_repository())
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    app = create_app(
        settings=settings,
        database=database,
        github_client=FakeGitHub(),  # type: ignore[arg-type]
        vector_store=FakeVectors(),
        embedding_provider=FakeEmbeddings(),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/repositories/{repository_id}/questions",
            headers={
                "Authorization": (
                    f"Bearer {TokenService(settings).issue_access_token(owner_id).value}"
                )
            },
            json={"question": question},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["tool_call_count"] == 2
    assert [item["tool"] for item in body["trace"]] == expected_tools
    assert [item["source_type"] for item in body["citations"]] == expected_citation_types


@pytest.mark.parametrize(
    ("mutation", "answerability", "failure_code"),
    [
        (add_ambiguous_target, "insufficient_evidence", "caller_target_ambiguous"),
        (invalidate_graph, "temporarily_unavailable", "call_graph_unavailable"),
    ],
)
def test_ambiguous_target_and_unavailable_graph_are_distinguished(
    mutation: Callable[[uuid.UUID], Coroutine[Any, Any, None]],
    answerability: str,
    failure_code: str,
) -> None:
    settings = make_settings()
    owner_id, _, repository_id = asyncio.run(seed_active_repository())
    asyncio.run(mutation(repository_id))
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    app = create_app(
        settings=settings,
        database=database,
        github_client=FakeGitHub(),  # type: ignore[arg-type]
        vector_store=FakeVectors(),
        embedding_provider=FakeEmbeddings(),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/repositories/{repository_id}/questions",
            headers={
                "Authorization": (
                    f"Bearer {TokenService(settings).issue_access_token(owner_id).value}"
                )
            },
            json={"question": "What calls validate?"},
        )

    assert response.status_code == 200
    assert response.json()["answerability"] == answerability
    assert response.json()["citations"] == []
    assert response.json()["trace"][0]["failure_code"] == failure_code


def test_history_question_uses_authorized_github_scope_and_mixed_schema() -> None:
    settings = make_settings()
    owner_id, _, repository_id = asyncio.run(seed_active_repository())
    github = FakeGitHub()
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    app = create_app(
        settings=settings,
        database=database,
        github_client=github,  # type: ignore[arg-type]
        vector_store=FakeVectors(),
        embedding_provider=FakeEmbeddings(),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/repositories/{repository_id}/questions",
            headers={
                "Authorization": (
                    f"Bearer {TokenService(settings).issue_access_token(owner_id).value}"
                )
            },
            json={"question": "Which commit introduced validation?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["answerability"] == "answered"
    assert body["tool_call_count"] == 1
    assert body["trace"][0]["tool"] == "get_history"
    assert body["citations"][0]["source_type"] == "commit"
    assert body["citations"][0]["commit_sha"] == "d" * 40
    assert body["citations"][1]["source_type"] == "pull_request"
    assert body["citations"][1]["number"] == 42
    assert github.repository_token_requests == [(501, 9001)]
    assert github.history_requests == [
        {
            "owner": "owner",
            "repository": "private-repo",
            "revision": "a" * 40,
            "limit": 3,
        }
    ]


@pytest.mark.parametrize("return_evidence", [False, True])
def test_no_evidence_or_unknown_model_citation_returns_explicit_nonanswer(
    return_evidence: bool,
) -> None:
    settings = make_settings()
    owner_id, _, repository_id = asyncio.run(seed_active_repository())
    embeddings = FakeEmbeddings()
    vectors = FakeVectors(return_evidence=return_evidence)
    llm = FakeLLM(evidence_ids=["E999"])
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    app = create_app(
        settings=settings,
        database=database,
        github_client=FakeGitHub(),  # type: ignore[arg-type]
        vector_store=vectors,
        embedding_provider=embeddings,  # type: ignore[arg-type]
        llm_provider=llm,
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/repositories/{repository_id}/questions",
            headers={
                "Authorization": (
                    f"Bearer {TokenService(settings).issue_access_token(owner_id).value}"
                )
            },
            json={"question": "How does validate inspect the response?"},
        )

    assert response.status_code == 200
    assert response.json()["answerability"] == "insufficient_evidence"
    assert response.json()["citations"] == []
    assert "validity flag" not in response.json()["answer"]
    assert len(llm.requests) == int(return_evidence)


def test_invalid_question_is_safely_rejected_before_retrieval() -> None:
    settings = make_settings()
    owner_id, _, repository_id = asyncio.run(seed_active_repository())
    embeddings = FakeEmbeddings()
    vectors = FakeVectors()
    llm = FakeLLM()
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    app = create_app(
        settings=settings,
        database=database,
        github_client=FakeGitHub(),  # type: ignore[arg-type]
        vector_store=vectors,
        embedding_provider=embeddings,  # type: ignore[arg-type]
        llm_provider=llm,
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/repositories/{repository_id}/questions",
            headers={
                "Authorization": (
                    f"Bearer {TokenService(settings).issue_access_token(owner_id).value}"
                )
            },
            json={"question": "unsafe\u0000question"},
        )

    assert response.status_code == 422
    assert "unsafe" not in response.text
    assert embeddings.queries == []
    assert vectors.scopes == []
    assert llm.requests == []


@pytest.mark.parametrize(
    ("repository_state", "expected_status", "expected_answerability"),
    [
        ("suspended_installation", 404, None),
        ("deleted_installation", 404, None),
        ("deleted_repository", 404, None),
        ("not_indexed", 200, "insufficient_evidence"),
        ("incomplete_build", 200, "insufficient_evidence"),
        ("stale_version", 200, "insufficient_evidence"),
        ("incompatible_model", 200, "insufficient_evidence"),
    ],
)
def test_repository_question_fails_closed_for_revoked_or_unsearchable_state(
    repository_state: str,
    expected_status: int,
    expected_answerability: str | None,
) -> None:
    settings = make_settings()
    owner_id, _, repository_id = asyncio.run(seed_active_repository())
    asyncio.run(mutate_repository_state(repository_id, repository_state))
    embeddings = FakeEmbeddings()
    vectors = FakeVectors()
    llm = FakeLLM()
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    app = create_app(
        settings=settings,
        database=database,
        github_client=FakeGitHub(),  # type: ignore[arg-type]
        vector_store=vectors,
        embedding_provider=embeddings,  # type: ignore[arg-type]
        llm_provider=llm,
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/repositories/{repository_id}/questions",
            headers={
                "Authorization": (
                    f"Bearer {TokenService(settings).issue_access_token(owner_id).value}"
                )
            },
            json={"question": "How does validate inspect the response?"},
        )

    assert response.status_code == expected_status
    if expected_answerability is not None:
        assert response.json()["answerability"] == expected_answerability
    assert embeddings.queries == []
    assert vectors.scopes == []
    assert llm.requests == []


def test_repository_question_keeps_using_prior_active_version_during_replacement() -> None:
    settings = make_settings()
    owner_id, _, repository_id = asyncio.run(seed_active_repository())
    asyncio.run(mutate_repository_state(repository_id, "replacement_building"))
    embeddings = FakeEmbeddings()
    vectors = FakeVectors()
    llm = FakeLLM()
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    app = create_app(
        settings=settings,
        database=database,
        github_client=FakeGitHub(),  # type: ignore[arg-type]
        vector_store=vectors,
        embedding_provider=embeddings,  # type: ignore[arg-type]
        llm_provider=llm,
    )
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/repositories/{repository_id}/questions",
            headers={
                "Authorization": (
                    f"Bearer {TokenService(settings).issue_access_token(owner_id).value}"
                )
            },
            json={"question": "How does validate inspect the response?"},
        )

    assert response.status_code == 200
    assert response.json()["answerability"] == "answered"
    assert response.json()["active_index_version"] == 1
    assert response.json()["indexed_commit_sha"] == "a" * 40
    assert vectors.scopes[0].index_version == 1


def test_repository_question_requires_authentication_and_rejects_extra_fields() -> None:
    settings = make_settings()
    owner_id, _, repository_id = asyncio.run(seed_active_repository())
    database = Database(
        engine=create_async_engine(database_url(), pool_pre_ping=True),
        ready_timeout_seconds=2,
    )
    app = create_app(
        settings=settings,
        database=database,
        github_client=FakeGitHub(),  # type: ignore[arg-type]
        vector_store=FakeVectors(),
        embedding_provider=FakeEmbeddings(),  # type: ignore[arg-type]
        llm_provider=FakeLLM(),
    )
    with TestClient(app) as client:
        unauthenticated = client.post(
            f"/api/v1/repositories/{repository_id}/questions",
            json={"question": "Where is validate?"},
        )
        malformed = client.post(
            f"/api/v1/repositories/{repository_id}/questions",
            headers={
                "Authorization": (
                    f"Bearer {TokenService(settings).issue_access_token(owner_id).value}"
                )
            },
            json={"question": "Where is validate?", "filters": {}},
        )

    assert unauthenticated.status_code == 401
    assert malformed.status_code == 422
    assert "Where is validate?" not in malformed.text
