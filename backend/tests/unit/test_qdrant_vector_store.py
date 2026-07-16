"""Deterministic vector identity and mandatory trusted filters."""

import uuid

from app.embeddings.preprocessing import EmbeddingPreprocessor, PreparedEmbedding
from app.indexing.models import ChunkType, ContentChunk
from app.vector.qdrant import VectorScope, deterministic_point_id, scope_filter
from tests.conftest import make_settings


def prepared(repository_id: uuid.UUID, *, ordinal: int = 0) -> PreparedEmbedding:
    chunk = ContentChunk(
        repository_id=repository_id,
        index_version=2,
        ordinal=ordinal,
        file_path="app/service.py",
        language="python",
        chunk_type=ChunkType.FUNCTION,
        symbol_name="service",
        qualified_name="app.service.service",
        parent_qualified_name=None,
        heading_hierarchy=(),
        imports=(),
        decorators=(),
        signature="def service():",
        docstring=None,
        start_line=1,
        end_line=2,
        commit_sha="a" * 40,
        content_hash="b" * 64,
        content="def service():\n    pass",
    )
    return EmbeddingPreprocessor(make_settings()).prepare_chunk(chunk)


def test_point_ids_are_deterministic_and_do_not_cross_scope() -> None:
    installation = uuid.UUID("10000000-0000-0000-0000-000000000000")
    repository_a = uuid.UUID("20000000-0000-0000-0000-000000000000")
    repository_b = uuid.UUID("30000000-0000-0000-0000-000000000000")
    scope_a = VectorScope(installation, repository_a, 2)

    first = deterministic_point_id(scope_a, prepared(repository_a))
    assert first == deterministic_point_id(scope_a, prepared(repository_a))
    assert first != deterministic_point_id(
        VectorScope(installation, repository_b, 2), prepared(repository_b)
    )
    assert first != deterministic_point_id(
        VectorScope(installation, repository_a, 3), prepared(repository_a)
    )
    assert first != deterministic_point_id(scope_a, prepared(repository_a, ordinal=1))


def test_scope_filter_always_contains_installation_repository_and_version() -> None:
    scope = VectorScope(uuid.uuid4(), uuid.uuid4(), 7)
    built = scope_filter(scope).model_dump(mode="json")
    conditions = {item["key"]: item["match"]["value"] for item in built["must"]}
    assert conditions == {
        "installation_id": str(scope.installation_id),
        "repository_id": str(scope.repository_id),
        "index_version": 7,
    }
