"""Stable string enum values persisted by the foundational schema."""

from enum import StrEnum
from typing import TypeVar

from sqlalchemy import Enum as SQLAlchemyEnum


class GitHubAccountType(StrEnum):
    USER = "user"
    ORGANIZATION = "organization"


class InstallationStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class RepositorySelection(StrEnum):
    ALL = "all"
    SELECTED = "selected"


class InstallationMemberRole(StrEnum):
    OWNER = "owner"
    MEMBER = "member"


class RepositoryIndexingStatus(StrEnum):
    NOT_INDEXED = "not_indexed"
    QUEUED = "queued"
    CLONING = "cloning"
    DISCOVERING = "discovering"
    PARSING = "parsing"
    EMBEDDING = "embedding"
    BUILDING_GRAPH = "building_graph"
    FINALIZING = "finalizing"
    COMPLETE = "complete"
    FAILED = "failed"
    DELETING = "deleting"
    ACCESS_REVOKED = "access_revoked"


class IndexingJobType(StrEnum):
    INITIAL_INDEX = "initial_index"
    MANUAL_REINDEX = "manual_reindex"
    INCREMENTAL_REINDEX = "incremental_reindex"
    FULL_REBUILD = "full_rebuild"
    DELETE_REPOSITORY = "delete_repository"


class IndexingJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IndexBuildState(StrEnum):
    BUILDING = "building"
    READY = "ready"
    ACTIVE = "active"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class IndexCleanupStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    COMPLETE = "complete"


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM_EVENT = "system_event"


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    PARTIALLY_ANSWERED = "partially_answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    STALE_INDEX = "stale_index"
    TOOL_FAILURE = "tool_failure"
    UNSUPPORTED_QUESTION = "unsupported_question"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SymbolType(StrEnum):
    FUNCTION = "function"
    ASYNC_FUNCTION = "async_function"
    CLASS = "class"
    METHOD = "method"
    MODULE = "module"


class ResolutionType(StrEnum):
    EXACT_SAME_FILE = "exact_same_file"
    EXACT_DIRECT_IMPORT = "exact_direct_import"
    QUALIFIED_MODULE = "qualified_module"
    PROBABLE_METHOD = "probable_method"
    UNRESOLVED = "unresolved"


class WebhookDeliveryStatus(StrEnum):
    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSED = "processed"
    IGNORED = "ignored"
    FAILED = "failed"


StringEnum = TypeVar("StringEnum", bound=StrEnum)


def database_enum(enum_type: type[StringEnum], *, name: str) -> SQLAlchemyEnum:
    """Persist enum values as portable constrained strings, not PostgreSQL enum types."""
    return SQLAlchemyEnum(
        enum_type,
        name=name,
        values_callable=lambda members: [member.value for member in members],
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
    )
