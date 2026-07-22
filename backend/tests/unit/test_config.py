"""Configuration validation and redaction behavior."""

import pytest
from pydantic import ValidationError

from app.core.config import AppEnvironment, Settings, load_settings
from tests.conftest import make_settings, production_test_private_key

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
        {"parser_max_total_call_sites": 10, "parser_max_call_sites_per_file": 11},
        {"parser_timeout_seconds": 5, "parser_process_cpu_seconds": 6},
    ],
)
def test_parser_limits_are_validated_as_a_consistent_set(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        make_settings(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"agent_max_tool_calls": 5},
        {"agent_tool_timeout_seconds": 8.1},
        {"agent_provider_timeout_seconds": 45, "agent_total_timeout_seconds": 45},
        {"agent_max_tool_result_bytes": 2048, "agent_max_total_evidence_bytes": 1024},
    ],
)
def test_agent_limits_are_validated_as_a_consistent_set(
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
        database_url=(
            "postgresql+asyncpg://service:DatabaseProductionFixtureCredential@"
            "db.example.com/repolume?ssl=require"
        ),
        log_json=True,
        docs_enabled=False,
        cors_origins=["https://app.repolume.example"],
        trusted_hosts=["api.repolume.example"],
        github_oauth_callback_url="https://api.repolume.example/api/v1/auth/github/callback",
        frontend_url="https://app.repolume.example",
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
        ("database_url", "postgresql+asyncpg://service:secret@db.example.com/repolume"),
        (
            "database_url",
            "postgresql+asyncpg://service:secret@db.example.com/repolume?ssl=require",
        ),
        (
            "database_url",
            "postgresql+asyncpg://service:StrongProductionCredential@"
            "db.example.com/repolume?sslmode=require",
        ),
        ("github_oauth_callback_url", "http://api.repolume.example/callback"),
        ("frontend_url", None),
        ("frontend_url", "http://app.repolume.example"),
        ("frontend_url", "https://untrusted.repolume.example"),
        ("redis_url", "redis://service:secret@redis.example.com/0"),
        ("redis_url", "rediss://127.0.0.1/0"),
        ("redis_url", "rediss://redis.example.com/0"),
        ("redis_url", "rediss://service:secret@redis.example.com/0"),
        ("embedding_service_url", "http://embeddings.example.com"),
        ("qdrant_url", "http://qdrant.example.com"),
        ("qdrant_api_key", ""),
        ("llm_provider", "deterministic"),
        ("llm_api_url", "http://api.openai.com/v1"),
    ],
)
def test_production_settings_fail_closed(override: str, value: object) -> None:
    values: dict[str, object] = {
        "app_env": AppEnvironment.PRODUCTION,
        "database_url": (
            "postgresql+asyncpg://service:DatabaseProductionFixtureCredential@"
            "db.example.com/repolume?ssl=require"
        ),
        "redis_url": ("rediss://service:RedisProductionFixtureCredential@redis.example.com/0"),
        "log_json": True,
        "docs_enabled": False,
        "cors_origins": ["https://app.repolume.example"],
        "trusted_hosts": ["api.repolume.example"],
        "github_app_id": 12345,
        "github_client_id": "Iv1.production-fixture-client",
        "github_client_secret": "G1thubProductionFixtureCredential-0001",
        "github_app_private_key": production_test_private_key(),
        "github_webhook_secret": "Webh00kProductionFixtureCredential-001",
        "github_oauth_callback_url": "https://api.repolume.example/api/v1/auth/github/callback",
        "frontend_url": "https://app.repolume.example",
        "access_token_secret": "AccessProductionFixtureCredential-00001",
        "token_hash_secret": "HashProductionFixtureCredential-0000001",
        "embedding_service_url": "https://embeddings.example.com",
        "embedding_service_token": "Emb3ddingProductionFixtureCredential-001",
        "qdrant_url": "https://qdrant.example.com",
        "qdrant_api_key": "Qdr4ntProductionFixtureCredential-00001",
        "llm_api_key": "LlmProductionFixtureCredential-0000001",
    }
    values[override] = value

    with pytest.raises(ValidationError):
        Settings.model_validate(values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_api_url": "https://attacker.example/v1"},
        {"llm_api_key": ""},
        {"access_token_secret": "change-me-change-me-change-me-change-me"},
        {"github_public_api_token": "test-only-public-api-token-placeholder"},
        {"github_client_id": "abababababababababababababababab"},
        {"llm_api_url": "https://api.openai.com/v1?forward=unsafe"},
        {"cors_origins": ["https://app.repolume.example/not-an-origin"]},
        {
            "github_oauth_callback_url": (
                "https://api.repolume.example/api/v1/auth/github/callback?next=unsafe"
            )
        },
        {
            "github_oauth_callback_url": (
                "https://untrusted.repolume.example/api/v1/auth/github/callback"
            )
        },
    ],
)
def test_production_rejects_provider_exfiltration_and_placeholder_configuration(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "app_env": AppEnvironment.PRODUCTION,
        "log_json": True,
        "docs_enabled": False,
        "cors_origins": ["https://app.repolume.example"],
        "trusted_hosts": ["api.repolume.example"],
        "github_oauth_callback_url": ("https://api.repolume.example/api/v1/auth/github/callback"),
    }
    values.update(overrides)
    with pytest.raises(ValidationError):
        make_settings(**values)


@pytest.mark.parametrize(
    "llm_api_url",
    [
        "https://api.openai.com/v1",
        "https://generativelanguage.googleapis.com/v1beta/openai",
    ],
)
def test_production_accepts_only_reviewed_llm_provider_endpoints(llm_api_url: str) -> None:
    settings = make_settings(
        app_env=AppEnvironment.PRODUCTION,
        log_json=True,
        docs_enabled=False,
        cors_origins=["https://app.repolume.example"],
        trusted_hosts=["api.repolume.example"],
        github_oauth_callback_url="https://api.repolume.example/api/v1/auth/github/callback",
        llm_api_url=llm_api_url,
    )

    assert str(settings.llm_api_url).rstrip("/") == llm_api_url


def test_production_accepts_complete_google_configuration_with_exact_callback() -> None:
    settings = make_settings(
        app_env=AppEnvironment.PRODUCTION,
        log_json=True,
        docs_enabled=False,
        cors_origins=["https://app.repolume.example"],
        trusted_hosts=["api.repolume.example"],
        github_oauth_callback_url="https://api.repolume.example/api/v1/auth/github/callback",
        google_auth_enabled=True,
        google_client_id="GoogleProductionClientIdentifier-9241",
        google_client_secret="GoogleProductionCredential-8f53c10a9472",  # noqa: S106
        google_oauth_callback_url="https://api.repolume.example/api/v1/auth/google/callback",
    )

    assert settings.google_auth_enabled is True
