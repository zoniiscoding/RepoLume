"""Structured logging content minimization."""

import pytest
import structlog

from app.core.logging import configure_logging
from tests.conftest import make_settings


def test_startup_log_fields_exclude_database_secret(capsys: pytest.CaptureFixture[str]) -> None:
    secret = "logging-secret-sentinel"
    settings = make_settings(
        database_url=f"postgresql+asyncpg://service:{secret}@127.0.0.1/repolume"
    )
    configure_logging(level="INFO", render_json=True)

    structlog.get_logger("test").info("configuration_loaded", **settings.safe_summary())

    output = capsys.readouterr().out
    assert secret not in output
    assert "database_url" not in output
    assert '"event": "configuration_loaded"' in output


def test_log_level_filters_debug_output(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", render_json=True)
    log = structlog.get_logger("test")

    log.debug("must_not_appear")
    log.info("must_appear")

    output = capsys.readouterr().out
    assert "must_not_appear" not in output
    assert "must_appear" in output
