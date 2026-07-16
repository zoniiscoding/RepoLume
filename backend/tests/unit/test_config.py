"""Configuration validation and redaction behavior."""

import pytest
from pydantic import ValidationError

from app.core.config import AppEnvironment, Settings, load_settings
from tests.conftest import TEST_PRIVATE_KEY, make_settings

SECRET = "configuration-secret-sentinel"


def test_development_settings_accept_async_postgresql() -> None:
    settings = make_settings(
        app_env=AppEnvironment.DEVELOPMENT,
        log_json=False,
        docs_enabled=True,
    )

    assert settings.app_env is AppEnvironment.DEVELOPMENT
    assert settings.log_level == "INFO"
    assert settings.database_url.get_secret_value().startswith("postgresql+asyncpg://")


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite+aiosqlite:///test.db",
        "postgresql://user:pass@db.example/repolume",
        "postgresql+asyncpg://missing-database.example",
        "not a database url",
    ],
)
def test_database_url_rejects_unsupported_or_incomplete_values(database_url: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(database_url=database_url)


def test_log_level_is_normalized() -> None:
    assert make_settings(log_level="warning").log_level == "WARNING"


def test_log_level_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        make_settings(log_level="verbose")


def test_pool_limits_are_validated() -> None:
    with pytest.raises(ValidationError):
        make_settings(database_pool_size=0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"clone_max_file_bytes": 1024, "parser_max_input_bytes": 2048},
        {"parser_max_symbol_bytes": 1024, "parser_max_chunk_bytes": 2048},
        {"parser_max_document_section_bytes": 1024, "parser_max_chunk_bytes": 2048},
        {"parser_timeout_seconds": 5, "parser_process_cpu_seconds": 6},
    ],
)
def test_parser_limits_are_validated_as_a_consistent_set(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        make_settings(**overrides)


@pytest.mark.parametrize("redis_url", ["http://redis.example", "redis://", "not-a-url"])
def test_redis_url_rejects_unsupported_or_incomplete_values(redis_url: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(redis_url=redis_url)


def test_safe_summary_and_repr_do_not_contain_database_secret() -> None:
    settings = make_settings(database_url=f"postgresql+asyncpg://user:{SECRET}@127.0.0.1/repolume")

    assert SECRET not in repr(settings)
    assert SECRET not in str(settings.safe_summary())
    assert "database_url" not in settings.safe_summary()


def test_load_settings_raises_generic_error_without_rejected_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"invalid://user:{SECRET}@example.invalid/database")
    monkeypatch.setenv("APP_ENV", "test")

    with pytest.raises(RuntimeError) as captured:
        load_settings()

    assert str(captured.value) == "Application configuration is invalid"
    assert SECRET not in str(captured.value)


def test_production_settings_accept_secure_explicit_values() -> None:
    settings = make_settings(
        app_env=AppEnvironment.PRODUCTION,
        database_url="postgresql+asyncpg://service:secret@db.example.com/repolume",
        log_json=True,
        docs_enabled=False,
        cors_origins=["https://app.repolume.example"],
        trusted_hosts=["api.repolume.example"],
        github_oauth_callback_url="https://api.repolume.example/api/v1/auth/github/callback",
    )

    assert settings.is_production is True


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("log_json", False),
        ("docs_enabled", True),
        ("cors_origins", []),
        ("cors_origins", ["http://app.repolume.example"]),
        ("trusted_hosts", ["*"]),
        ("trusted_hosts", ["localhost"]),
        ("database_url", "postgresql+asyncpg://service:secret@127.0.0.1/repolume"),
        ("database_url", "postgresql+asyncpg://service@db.example.com/repolume"),
        ("github_oauth_callback_url", "http://api.repolume.example/callback"),
        ("redis_url", "redis://service:secret@redis.example.com/0"),
        ("redis_url", "rediss://127.0.0.1/0"),
        ("redis_url", "rediss://redis.example.com/0"),
    ],
)
def test_production_settings_fail_closed(override: str, value: object) -> None:
    values: dict[str, object] = {
        "app_env": AppEnvironment.PRODUCTION,
        "database_url": "postgresql+asyncpg://service:secret@db.example.com/repolume",
        "redis_url": "rediss://service:secret@redis.example.com/0",
        "log_json": True,
        "docs_enabled": False,
        "cors_origins": ["https://app.repolume.example"],
        "trusted_hosts": ["api.repolume.example"],
        "github_app_id": 12345,
        "github_client_id": "test-client-id",
        "github_client_secret": "github-client-secret-for-tests-only-000000",
        "github_app_private_key": TEST_PRIVATE_KEY,
        "github_webhook_secret": "github-webhook-secret-for-tests-only-0000",
        "github_oauth_callback_url": "https://api.repolume.example/api/v1/auth/github/callback",
        "access_token_secret": "access-token-secret-for-tests-only-0000000",
        "token_hash_secret": "token-hash-secret-for-tests-only-000000000",
    }
    values[override] = value

    with pytest.raises(ValidationError):
        Settings.model_validate(values)
