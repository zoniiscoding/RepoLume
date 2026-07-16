"""Controlled acceptance check for the immutable real ONNX model."""

import math
import os

import pytest

from app.model import FastEmbedModel
from tests.conftest import make_settings


@pytest.mark.model
def test_pinned_model_is_deterministic_normalized_and_768_dimensional() -> None:
    settings = make_settings(
        model_cache_dir=os.environ.get(
            "REPOLUME_TEST_MODEL_CACHE",
            "/tmp/repolume-test-model-cache",  # noqa: S108
        ),
        model_local_files_only=os.environ.get("HF_HUB_OFFLINE") == "1",
    )
    model = FastEmbedModel(settings)
    model.load()
    text = "def rotate_refresh_token(token: str) -> str:\n    return token"
    first = model.embed("document", [text])[0]
    second = model.embed("document", [text])[0]
    query = model.embed("query", ["Where is refresh token rotation implemented?"])[0]
    assert first == second
    assert len(first) == len(query) == 768
    assert math.isclose(math.sqrt(sum(value * value for value in first)), 1.0, abs_tol=1e-4)
    assert model.token_count(text) <= settings.model_max_tokens
    model.close()
