"""Explicit fake model and settings for private-service tests."""

import hashlib
import math
import time
from collections.abc import Sequence
from typing import Literal

import pytest

from app.config import Environment, Settings


class FakeModel:
    def __init__(
        self,
        *,
        load_failure: bool = False,
        load_delay: float = 0,
        embed_delay: float = 0,
        token_count: int = 10,
    ) -> None:
        self.load_failure = load_failure
        self.load_delay = load_delay
        self.embed_delay = embed_delay
        self.configured_token_count = token_count
        self.load_calls = 0
        self.embed_calls = 0
        self.closed = False

    def load(self) -> None:
        self.load_calls += 1
        time.sleep(self.load_delay)
        if self.load_failure:
            raise RuntimeError("private-model-load-detail")

    def token_count(self, text: str) -> int:
        del text
        return self.configured_token_count

    def embed(self, kind: Literal["document", "query"], texts: Sequence[str]) -> list[list[float]]:
        self.embed_calls += 1
        time.sleep(self.embed_delay)
        vectors: list[list[float]] = []
        for text in texts:
            seed = hashlib.sha256(f"{kind}:{text}".encode()).digest()
            vector = [0.0] * 768
            vector[seed[0] % 768] = 1.0
            assert math.isclose(sum(value * value for value in vector), 1.0)
            vectors.append(vector)
        return vectors

    def close(self) -> None:
        self.closed = True


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": Environment.TEST,
        "service_token": "embedding-service-test-token-000000000000",
        "log_json": True,
        "model_cache_dir": "/tmp/repolume-test-model-cache",  # noqa: S108
        "batch_size": 2,
        "max_request_bytes": 4096,
        "max_total_text_bytes": 2048,
        "max_text_bytes_per_document": 1024,
        "request_timeout_seconds": 1,
    }
    values.update(overrides)
    return Settings.model_validate(values)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def model() -> FakeModel:
    return FakeModel()
