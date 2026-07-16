"""Validated environment configuration with secret-safe representations."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

MINIMUM_SECRET_LENGTH = 32


class AppEnvironment(StrEnum):
    """Supported runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Application settings loaded exclusively from environment or local `.env`."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    app_name: str = "RepoLume API"
    app_env: AppEnvironment = AppEnvironment.DEVELOPMENT
    database_url: SecretStr
    redis_url: SecretStr
    log_level: str = "INFO"
    log_json: bool = False
    docs_enabled: bool = True

    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    database_ready_timeout_seconds: float = Field(default=2.0, gt=0, le=10)

    github_app_id: int = Field(gt=0)
    github_client_id: str = Field(min_length=1, max_length=255)
    github_client_secret: SecretStr
    github_app_private_key: SecretStr
    github_webhook_secret: SecretStr
    github_oauth_callback_url: AnyHttpUrl

    access_token_secret: SecretStr
    token_hash_secret: SecretStr
    access_token_ttl_seconds: int = Field(default=900, ge=300, le=1800)
    refresh_token_ttl_seconds: int = Field(default=2_592_000, ge=3600, le=7_776_000)
    oauth_state_ttl_seconds: int = Field(default=600, ge=120, le=900)
    installation_membership_ttl_seconds: int = Field(default=28_800, ge=900, le=86_400)
    refresh_cookie_name: str = Field(default="repolume_refresh_token", pattern=r"^[a-z0-9_]+$")

    worker_stream_name: str = Field(default="repolume:indexing", pattern=r"^[a-z0-9:_-]+$")
    worker_consumer_group: str = Field(default="repolume-workers", pattern=r"^[a-z0-9:_-]+$")
    worker_poll_timeout_ms: int = Field(default=1_000, ge=100, le=30_000)
    worker_reconcile_interval_seconds: float = Field(default=5.0, ge=0.1, le=60)
    worker_heartbeat_interval_seconds: float = Field(default=5.0, ge=0.1, le=60)
    worker_abandoned_after_seconds: int = Field(default=30, ge=5, le=600)
    worker_max_attempts: int = Field(default=3, ge=1, le=10)
    worker_retry_base_seconds: int = Field(default=2, ge=1, le=300)
    worker_retry_max_seconds: int = Field(default=60, ge=1, le=3_600)
    worker_stream_max_length: int = Field(default=10_000, ge=100, le=1_000_000)

    clone_git_executable: Path = Path("/usr/bin/git")
    clone_timeout_seconds: int = Field(default=120, ge=5, le=900)
    clone_max_repository_bytes: int = Field(default=500 * 1024 * 1024, ge=1024)
    clone_max_file_bytes: int = Field(default=2 * 1024 * 1024, ge=1024)
    clone_max_file_count: int = Field(default=20_000, ge=1)
    clone_max_discovered_bytes: int = Field(default=250 * 1024 * 1024, ge=1024)
    clone_process_memory_bytes: int = Field(default=1024 * 1024 * 1024, ge=64 * 1024 * 1024)
    clone_process_cpu_seconds: int = Field(default=120, ge=5, le=900)

    parser_max_input_bytes: int = Field(default=2 * 1024 * 1024, ge=1024)
    parser_max_symbols_per_file: int = Field(default=5_000, ge=1, le=100_000)
    parser_max_symbol_bytes: int = Field(default=512 * 1024, ge=1024)
    parser_max_chunk_bytes: int = Field(default=32 * 1024, ge=512)
    parser_max_chunks_per_file: int = Field(default=2_000, ge=1, le=100_000)
    parser_max_total_chunks: int = Field(default=50_000, ge=1, le=1_000_000)
    parser_max_total_chunk_bytes: int = Field(default=64 * 1024 * 1024, ge=1024)
    parser_max_document_section_bytes: int = Field(default=256 * 1024, ge=1024)
    parser_max_warnings_per_file: int = Field(default=50, ge=1, le=1_000)
    parser_timeout_seconds: float = Field(default=180.0, gt=0, le=900)
    parser_process_memory_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=64 * 1024 * 1024)
    parser_process_cpu_seconds: int = Field(default=120, ge=5, le=900)

    embedding_service_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:8100")
    embedding_service_token: SecretStr
    embedding_model_identifier: str = "jinaai/jina-embeddings-v2-base-code"
    embedding_model_revision: str = "516f4baf13dec4ddddda8631e019b5737c8bc250"
    embedding_dimension: int = Field(default=768, ge=1, le=65_536)
    embedding_preprocessing_version: str = Field(
        default="repolume-embedding-v1",
        pattern=r"^[a-z0-9._-]+$",
    )
    embedding_batch_size: int = Field(default=16, ge=1, le=256)
    embedding_max_document_bytes: int = Field(default=48 * 1024, ge=1024)
    embedding_connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    embedding_read_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    embedding_max_attempts: int = Field(default=3, ge=1, le=10)
    embedding_retry_base_seconds: float = Field(default=0.25, gt=0, le=10)

    qdrant_url: AnyHttpUrl = AnyHttpUrl("http://127.0.0.1:6333")
    qdrant_api_key: SecretStr = SecretStr("")
    qdrant_collection_name: str = Field(
        default="repolume_chunks",
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    qdrant_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    qdrant_upsert_batch_size: int = Field(default=128, ge=1, le=1_000)
    qdrant_max_attempts: int = Field(default=3, ge=1, le=10)
    qdrant_retry_base_seconds: float = Field(default=0.25, gt=0, le=10)

    cors_origins: list[AnyHttpUrl] = Field(default_factory=list)
    trusted_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        """Require the async PostgreSQL SQLAlchemy driver and a named database."""
        raw_url = value.get_secret_value()
        try:
            parsed = make_url(raw_url)
        except ArgumentError as error:
            raise ValueError("DATABASE_URL must be a valid SQLAlchemy URL") from error
        if parsed.drivername != "postgresql+asyncpg":
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg driver")
        if not parsed.host or not parsed.database:
            raise ValueError("DATABASE_URL must include a host and database name")
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: SecretStr) -> SecretStr:
        """Require a network Redis URL without exposing its credentials."""
        parsed = urlsplit(value.get_secret_value())
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
            raise ValueError("REDIS_URL must use redis:// or rediss:// and include a host")
        return value

    @field_validator("clone_git_executable")
    @classmethod
    def validate_git_executable(cls, value: Path) -> Path:
        """Use an explicit absolute Git binary, never PATH lookup."""
        if not value.is_absolute():
            raise ValueError("CLONE_GIT_EXECUTABLE must be an absolute path")
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Normalize and restrict logging levels."""
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL is not supported")
        return normalized

    @field_validator(
        "github_client_secret",
        "github_webhook_secret",
        "access_token_secret",
        "token_hash_secret",
        "embedding_service_token",
    )
    @classmethod
    def validate_secret_length(cls, value: SecretStr) -> SecretStr:
        """Reject secrets that do not provide a minimally useful entropy budget."""
        if len(value.get_secret_value()) < MINIMUM_SECRET_LENGTH:
            raise ValueError(
                "Configured authentication secrets must contain at least 32 characters"
            )
        return value

    @field_validator("github_app_private_key")
    @classmethod
    def validate_private_key(cls, value: SecretStr) -> SecretStr:
        """Require a PEM-shaped private key without ever returning its content."""
        raw_value = value.get_secret_value()
        if "-----BEGIN" not in raw_value or "PRIVATE KEY-----" not in raw_value:
            raise ValueError("GITHUB_APP_PRIVATE_KEY must be PEM encoded")
        return value

    @field_validator("trusted_hosts")
    @classmethod
    def validate_trusted_hosts(cls, value: list[str]) -> list[str]:
        """Reject empty host entries."""
        if not value or any(not host.strip() for host in value):
            raise ValueError("TRUSTED_HOSTS must contain explicit non-empty hosts")
        return value

    @model_validator(mode="after")
    def validate_production_security(self) -> Self:
        """Fail closed when production-only security settings are unsafe."""
        if not self.is_production:
            return self

        if not self.log_json:
            raise ValueError("LOG_JSON must be true in production")
        if self.docs_enabled:
            raise ValueError("DOCS_ENABLED must be false in production")
        if not self.cors_origins:
            raise ValueError("CORS_ORIGINS must be explicit in production")
        if any(origin.scheme != "https" for origin in self.cors_origins):
            raise ValueError("CORS_ORIGINS must use HTTPS in production")
        if self.github_oauth_callback_url.scheme != "https":
            raise ValueError("GITHUB_OAUTH_CALLBACK_URL must use HTTPS in production")

        self._validate_production_backing_services()
        return self

    def _validate_production_backing_services(self) -> None:
        """Require authenticated remote infrastructure over encrypted transports."""

        forbidden_hosts = {"*", "localhost", "127.0.0.1", "0.0.0.0"}
        if any(host.lower() in forbidden_hosts for host in self.trusted_hosts):
            raise ValueError("TRUSTED_HOSTS contains a development-only host")

        database_url = make_url(self.database_url.get_secret_value())
        if database_url.host in forbidden_hosts:
            raise ValueError("DATABASE_URL cannot target a local host in production")
        if database_url.password is None:
            raise ValueError("DATABASE_URL must contain managed credentials in production")
        redis_url = urlsplit(self.redis_url.get_secret_value())
        if redis_url.scheme != "rediss":
            raise ValueError("REDIS_URL must use TLS in production")
        if redis_url.hostname in forbidden_hosts or redis_url.password is None:
            raise ValueError("REDIS_URL must contain managed remote credentials in production")
        if self.embedding_service_url.scheme != "https":
            raise ValueError("EMBEDDING_SERVICE_URL must use HTTPS in production")
        if self.qdrant_url.scheme != "https":
            raise ValueError("QDRANT_URL must use HTTPS in production")
        if len(self.qdrant_api_key.get_secret_value()) < MINIMUM_SECRET_LENGTH:
            raise ValueError("QDRANT_API_KEY must contain a production credential")

    @model_validator(mode="after")
    def validate_parser_limits(self) -> Self:
        """Keep parsing and chunking bounds internally consistent."""
        if self.parser_max_input_bytes > self.clone_max_file_bytes:
            raise ValueError("PARSER_MAX_INPUT_BYTES cannot exceed CLONE_MAX_FILE_BYTES")
        if self.parser_max_chunk_bytes > self.parser_max_symbol_bytes:
            raise ValueError("PARSER_MAX_CHUNK_BYTES cannot exceed PARSER_MAX_SYMBOL_BYTES")
        if self.parser_max_chunk_bytes > self.parser_max_document_section_bytes:
            raise ValueError(
                "PARSER_MAX_CHUNK_BYTES cannot exceed PARSER_MAX_DOCUMENT_SECTION_BYTES"
            )
        if self.parser_process_cpu_seconds > self.parser_timeout_seconds:
            raise ValueError("PARSER_PROCESS_CPU_SECONDS cannot exceed PARSER_TIMEOUT_SECONDS")
        if self.embedding_max_document_bytes < self.parser_max_chunk_bytes:
            raise ValueError(
                "EMBEDDING_MAX_DOCUMENT_BYTES cannot be smaller than PARSER_MAX_CHUNK_BYTES"
            )
        return self

    @property
    def is_production(self) -> bool:
        """Return whether production safeguards are required."""
        return self.app_env is AppEnvironment.PRODUCTION

    def safe_summary(self) -> dict[str, Any]:
        """Return the only configuration fields allowed in startup logs."""
        return {
            "app_env": self.app_env.value,
            "log_level": self.log_level,
            "log_json": self.log_json,
            "docs_enabled": self.docs_enabled,
            "cors_origin_count": len(self.cors_origins),
            "trusted_host_count": len(self.trusted_hosts),
            "access_token_ttl_seconds": self.access_token_ttl_seconds,
            "membership_ttl_seconds": self.installation_membership_ttl_seconds,
            "worker_max_attempts": self.worker_max_attempts,
            "clone_timeout_seconds": self.clone_timeout_seconds,
            "parser_max_input_bytes": self.parser_max_input_bytes,
            "parser_max_total_chunks": self.parser_max_total_chunks,
            "parser_max_total_chunk_bytes": self.parser_max_total_chunk_bytes,
            "parser_timeout_seconds": self.parser_timeout_seconds,
            "embedding_model_identifier": self.embedding_model_identifier,
            "embedding_model_revision": self.embedding_model_revision,
            "embedding_dimension": self.embedding_dimension,
            "embedding_batch_size": self.embedding_batch_size,
            "qdrant_collection_name": self.qdrant_collection_name,
        }


def load_settings() -> Settings:
    """Load settings without leaking rejected values through startup errors."""
    try:
        return Settings()
    except ValidationError:
        raise RuntimeError("Application configuration is invalid") from None
