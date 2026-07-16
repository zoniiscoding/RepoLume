"""Validated environment configuration with secret-safe representations."""

from enum import StrEnum
from typing import Any, Self

from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


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
    log_level: str = "INFO"
    log_json: bool = False
    docs_enabled: bool = True

    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_pool_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    database_ready_timeout_seconds: float = Field(default=2.0, gt=0, le=10)

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

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        """Normalize and restrict logging levels."""
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL is not supported")
        return normalized

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

        forbidden_hosts = {"*", "localhost", "127.0.0.1", "0.0.0.0"}
        if any(host.lower() in forbidden_hosts for host in self.trusted_hosts):
            raise ValueError("TRUSTED_HOSTS contains a development-only host")

        database_url = make_url(self.database_url.get_secret_value())
        if database_url.host in forbidden_hosts:
            raise ValueError("DATABASE_URL cannot target a local host in production")
        if database_url.password is None:
            raise ValueError("DATABASE_URL must contain managed credentials in production")
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
        }


def load_settings() -> Settings:
    """Load settings without leaking rejected values through startup errors."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError:
        raise RuntimeError("Application configuration is invalid") from None
