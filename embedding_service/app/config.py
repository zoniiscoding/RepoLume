"""Validated private-service configuration."""

from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.constants import MODEL_DIMENSION, MODEL_IDENTIFIER, MODEL_MAX_TOKENS, MODEL_REVISION

_MINIMUM_TOKEN_LENGTH = 32
_DEFAULT_MODEL_CACHE = Path("/tmp/repolume-models")  # noqa: S108 -- container-owned cache
_PLACEHOLDER_MARKERS = ("change-me", "ci-only", "placeholder", "replace-me", "test-only")


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        env_prefix="EMBEDDING_",
        case_sensitive=False,
        extra="ignore",
    )

    environment: Environment = Environment.DEVELOPMENT
    service_token: SecretStr
    log_level: str = "INFO"
    log_json: bool = False
    model_identifier: str = MODEL_IDENTIFIER
    model_revision: str = MODEL_REVISION
    model_dimension: int = MODEL_DIMENSION
    model_max_tokens: int = MODEL_MAX_TOKENS
    model_cache_dir: Path = _DEFAULT_MODEL_CACHE
    model_local_files_only: bool = False
    model_threads: int = Field(default=2, ge=1, le=32)
    batch_size: int = Field(default=16, ge=1, le=256)
    max_documents_per_request: int = Field(default=32, ge=1, le=256)
    max_text_bytes_per_document: int = Field(default=48 * 1024, ge=1024)
    max_total_text_bytes: int = Field(default=1024 * 1024, ge=1024)
    max_request_bytes: int = Field(default=1200 * 1024, ge=1024)
    request_timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    max_concurrent_requests: int = Field(default=2, ge=1, le=32)

    @field_validator("service_token")
    @classmethod
    def validate_service_token(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < _MINIMUM_TOKEN_LENGTH:
            raise ValueError("EMBEDDING_SERVICE_TOKEN must contain at least 32 characters")
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("EMBEDDING_LOG_LEVEL is not supported")
        return normalized

    @field_validator("model_cache_dir")
    @classmethod
    def validate_model_cache_dir(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("EMBEDDING_MODEL_CACHE_DIR must be an absolute path")
        return value

    @model_validator(mode="after")
    def validate_model_and_limits(self) -> Self:
        if (
            self.model_identifier != MODEL_IDENTIFIER
            or self.model_revision != MODEL_REVISION
            or self.model_dimension != MODEL_DIMENSION
            or self.model_max_tokens != MODEL_MAX_TOKENS
        ):
            raise ValueError("Embedding model identity must match the reviewed immutable baseline")
        if self.batch_size > self.max_documents_per_request:
            raise ValueError("EMBEDDING_BATCH_SIZE cannot exceed the request document limit")
        if self.max_text_bytes_per_document > self.max_total_text_bytes:
            raise ValueError("Per-document bytes cannot exceed total request text bytes")
        if self.max_total_text_bytes > self.max_request_bytes:
            raise ValueError("Total text bytes cannot exceed the HTTP request limit")
        if self.environment is Environment.PRODUCTION and not self.log_json:
            raise ValueError("EMBEDDING_LOG_JSON must be true in production")
        if self.environment is Environment.PRODUCTION:
            token = self.service_token.get_secret_value().strip().casefold()
            if any(marker in token for marker in _PLACEHOLDER_MARKERS):
                raise ValueError(
                    "EMBEDDING_SERVICE_TOKEN must not use a placeholder value in production"
                )
            if not self.model_local_files_only:
                raise ValueError("EMBEDDING_MODEL_LOCAL_FILES_ONLY must be true in production")
        return self

    def safe_summary(self) -> dict[str, Any]:
        return {
            "environment": self.environment.value,
            "log_level": self.log_level,
            "model_identifier": self.model_identifier,
            "model_revision": self.model_revision,
            "model_dimension": self.model_dimension,
            "model_max_tokens": self.model_max_tokens,
            "model_local_files_only": self.model_local_files_only,
            "batch_size": self.batch_size,
            "max_documents_per_request": self.max_documents_per_request,
            "max_text_bytes_per_document": self.max_text_bytes_per_document,
            "max_total_text_bytes": self.max_total_text_bytes,
            "max_concurrent_requests": self.max_concurrent_requests,
        }


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError:
        raise RuntimeError("Embedding service configuration is invalid") from None
