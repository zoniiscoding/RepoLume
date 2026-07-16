"""Central deterministic preprocessing for code, documentation, and queries."""

import hashlib
import json
from dataclasses import dataclass

from app.core.config import Settings
from app.indexing.failures import IndexingError
from app.indexing.models import ChunkType, ContentChunk


@dataclass(frozen=True, slots=True)
class PreparedEmbedding:
    """One bounded model input tied to its original trusted chunk metadata."""

    item_id: str
    text: str
    fingerprint: str
    chunk: ContentChunk | None


class EmbeddingPreprocessor:
    """Build stable plain-text model inputs without interpreting repository text."""

    def __init__(self, settings: Settings) -> None:
        self._model_identifier = settings.embedding_model_identifier
        self._model_revision = settings.embedding_model_revision
        self._dimension = settings.embedding_dimension
        self._version = settings.embedding_preprocessing_version
        self._max_document_bytes = settings.embedding_max_document_bytes

    @property
    def policy_fingerprint(self) -> str:
        configuration = {
            "dimension": self._dimension,
            "max_document_bytes": self._max_document_bytes,
            "model_identifier": self._model_identifier,
            "model_revision": self._model_revision,
            "preprocessing_version": self._version,
        }
        return hashlib.sha256(self._canonical_json(configuration).encode()).hexdigest()

    def prepare_chunk(self, chunk: ContentChunk) -> PreparedEmbedding:
        """Preserve trusted metadata and the complete chunk; reject rather than truncate."""
        content_kind = (
            "documentation"
            if chunk.chunk_type in {ChunkType.MARKDOWN, ChunkType.DOCUMENTATION}
            else "code"
        )
        metadata: dict[str, object] = {
            "chunk_type": chunk.chunk_type.value,
            "content_kind": content_kind,
            "decorators": list(chunk.decorators),
            "end_line": chunk.end_line,
            "file_path": chunk.file_path,
            "heading_hierarchy": list(chunk.heading_hierarchy),
            "language": chunk.language,
            "parent_symbol": chunk.parent_qualified_name,
            "qualified_symbol": chunk.qualified_name,
            "signature": chunk.signature,
            "start_line": chunk.start_line,
            "symbol": chunk.symbol_name,
        }
        text = "\n".join(
            (
                f"repolume_preprocessing={self._version}",
                f"chunk_metadata={self._canonical_json(metadata)}",
                "chunk_content_begin",
                chunk.content,
                "chunk_content_end",
            )
        )
        self._assert_size(text)
        item_id = str(chunk.ordinal)
        fingerprint = hashlib.sha256(f"{self.policy_fingerprint}\n{text}".encode()).hexdigest()
        return PreparedEmbedding(
            item_id=item_id,
            text=text,
            fingerprint=fingerprint,
            chunk=chunk,
        )

    def prepare_query(self, query: str) -> PreparedEmbedding:
        """Use the same model and versioned preprocessing policy for a bounded query."""
        text = "\n".join(
            (
                f"repolume_preprocessing={self._version}",
                "content_kind=query",
                "query_content_begin",
                query,
                "query_content_end",
            )
        )
        self._assert_size(text)
        fingerprint = hashlib.sha256(f"{self.policy_fingerprint}\n{text}".encode()).hexdigest()
        return PreparedEmbedding(
            item_id="query",
            text=text,
            fingerprint=fingerprint,
            chunk=None,
        )

    def _assert_size(self, text: str) -> None:
        if len(text.encode("utf-8")) > self._max_document_bytes:
            raise IndexingError(
                code="embedding_document_too_large",
                message="A repository chunk exceeds the embedding input limit",
                retryable=False,
            )

    @staticmethod
    def _canonical_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
