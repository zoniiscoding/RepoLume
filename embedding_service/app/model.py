"""Pinned ONNX embedding-model provider without remote code execution."""

from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol

from fastembed import TextEmbedding
from huggingface_hub import snapshot_download

from app.config import Settings
from app.constants import MODEL_ALLOW_PATTERNS


class EmbeddingModelProtocol(Protocol):
    def load(self) -> None: ...

    def token_count(self, text: str) -> int: ...

    def embed(
        self, kind: Literal["document", "query"], texts: Sequence[str]
    ) -> list[list[float]]: ...

    def close(self) -> None: ...


class FastEmbedModel:
    """Load exactly one reviewed model revision and reuse its CPU ONNX session."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: TextEmbedding | None = None

    def load(self) -> None:
        snapshot = snapshot_download(
            repo_id=self._settings.model_identifier,
            revision=self._settings.model_revision,
            allow_patterns=list(MODEL_ALLOW_PATTERNS),
            cache_dir=self._settings.model_cache_dir,
            local_files_only=self._settings.model_local_files_only,
        )
        self._model = TextEmbedding(
            model_name=self._settings.model_identifier,
            cache_dir=str(self._settings.model_cache_dir),
            threads=self._settings.model_threads,
            providers=["CPUExecutionProvider"],
            specific_model_path=str(Path(snapshot)),
        )

    def token_count(self, text: str) -> int:
        return self._required_model().token_count(text)

    def embed(self, kind: Literal["document", "query"], texts: Sequence[str]) -> list[list[float]]:
        model = self._required_model()
        generated = (
            model.query_embed(texts, batch_size=self._settings.batch_size)
            if kind == "query"
            else model.passage_embed(texts, batch_size=self._settings.batch_size)
        )
        return [embedding.astype(float).tolist() for embedding in generated]

    def close(self) -> None:
        self._model = None

    def _required_model(self) -> TextEmbedding:
        if self._model is None:
            raise RuntimeError("model_not_loaded")
        return self._model
