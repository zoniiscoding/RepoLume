"""Thin ASGI and worker entrypoint startup and cleanup contracts."""

import asyncio
import importlib
import sys
from collections.abc import Coroutine
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

import app.application as application_module
import app.worker as worker_module
from tests.conftest import make_settings


def test_asgi_entrypoint_constructs_exactly_one_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    create_app = Mock(return_value=sentinel)
    monkeypatch.setattr(application_module, "create_app", create_app)
    monkeypatch.delitem(sys.modules, "app.main", raising=False)

    module = importlib.import_module("app.main")

    assert isinstance(module, ModuleType)
    assert module.app is sentinel
    create_app.assert_called_once_with()


@pytest.mark.asyncio
async def test_worker_startup_wires_dependencies_and_always_closes_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings()
    database = SimpleNamespace(dispose=AsyncMock())
    queue = SimpleNamespace(close=AsyncMock())
    embeddings = SimpleNamespace(close=AsyncMock())
    vectors = SimpleNamespace(close=AsyncMock())
    github = SimpleNamespace(close=AsyncMock())
    store = object()
    indexing_worker = SimpleNamespace(run=AsyncMock(side_effect=RuntimeError("worker stopped")))
    configure_logging = Mock()
    captured: dict[str, object] = {}

    monkeypatch.setattr(worker_module, "load_settings", lambda: settings)
    monkeypatch.setattr(worker_module, "configure_logging", configure_logging)
    monkeypatch.setattr(
        worker_module,
        "Database",
        SimpleNamespace(from_settings=lambda configured: database),
    )
    monkeypatch.setattr(
        worker_module,
        "RedisJobQueue",
        SimpleNamespace(from_settings=lambda configured: queue),
    )
    monkeypatch.setattr(worker_module, "EmbeddingServiceClient", lambda configured: embeddings)
    monkeypatch.setattr(worker_module, "QdrantVectorStore", lambda configured: vectors)
    monkeypatch.setattr(worker_module, "GitHubClient", lambda configured: github)
    monkeypatch.setattr(worker_module, "GitHubRepositoryCloner", lambda configured: object())
    monkeypatch.setattr(worker_module, "FileDiscovery", lambda configured: object())
    monkeypatch.setattr(worker_module, "ProcessIsolatedAnalyzer", lambda configured: object())
    monkeypatch.setattr(
        worker_module,
        "IndexingJobStore",
        lambda configured_database, configured_settings: store,
    )

    def build_worker(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        return indexing_worker

    monkeypatch.setattr(worker_module, "IndexingWorker", build_worker)

    with pytest.raises(RuntimeError, match="worker stopped"):
        await worker_module.run_worker()

    configure_logging.assert_called_once_with(
        level=settings.log_level, render_json=settings.log_json
    )
    assert captured["settings"] is settings
    assert captured["queue"] is queue
    assert captured["store"] is store
    assert captured["github"] is github
    assert captured["embeddings"] is embeddings
    assert captured["vectors"] is vectors
    indexing_worker.run.assert_awaited_once_with()
    queue.close.assert_awaited_once_with()
    embeddings.close.assert_awaited_once_with()
    vectors.close.assert_awaited_once_with()
    github.close.assert_awaited_once_with()
    database.dispose.assert_awaited_once_with()


def test_worker_cli_treats_keyboard_interrupt_as_clean_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoked = False

    def interrupt(coroutine: Coroutine[Any, Any, None]) -> None:
        nonlocal invoked
        invoked = True
        coroutine.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "run", interrupt)

    worker_module.main()

    assert invoked is True
