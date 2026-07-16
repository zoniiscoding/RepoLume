"""Typed, transient models for static parsing and deterministic chunk construction."""

import uuid
from dataclasses import dataclass
from enum import StrEnum

from app.db.models.enums import SymbolType


class ParseStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    MALFORMED = "malformed"
    SKIPPED = "skipped"


class ParameterKind(StrEnum):
    POSITIONAL_ONLY = "positional_only"
    POSITIONAL_OR_KEYWORD = "positional_or_keyword"
    VAR_POSITIONAL = "var_positional"
    KEYWORD_ONLY = "keyword_only"
    VAR_KEYWORD = "var_keyword"


class ChunkType(StrEnum):
    FUNCTION = "function"
    ASYNC_FUNCTION = "async_function"
    METHOD = "method"
    CLASS_OVERVIEW = "class_overview"
    MODULE = "module"
    MARKDOWN = "markdown"
    DOCUMENTATION = "documentation"


@dataclass(frozen=True, slots=True)
class SourceSegment:
    text: str
    start_line: int
    end_line: int
    node_type: str


@dataclass(frozen=True, slots=True)
class ImportAlias:
    name: str
    alias: str | None


@dataclass(frozen=True, slots=True)
class ParsedImport:
    module: str | None
    relative_level: int
    names: tuple[ImportAlias, ...]
    source_text: str
    start_line: int
    end_line: int


@dataclass(frozen=True, slots=True)
class ParsedParameter:
    name: str
    kind: ParameterKind
    annotation: str | None
    default: str | None


@dataclass(frozen=True, slots=True)
class ParsedSymbol:
    file_path: str
    language: str
    symbol_type: SymbolType
    name: str
    is_async: bool
    qualified_name: str
    parent_qualified_name: str | None
    decorators: tuple[str, ...]
    signature: str
    header_text: str
    header_start_line: int
    header_end_line: int
    parameters: tuple[ParsedParameter, ...]
    return_annotation: str | None
    docstring: str | None
    source_text: str
    body_segments: tuple[SourceSegment, ...]
    start_line: int
    end_line: int
    commit_sha: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class ParsedFile:
    file_path: str
    language: str
    module_name: str
    source_text: str
    imports: tuple[ParsedImport, ...]
    module_docstring: str | None
    module_segments: tuple[SourceSegment, ...]
    symbols: tuple[ParsedSymbol, ...]
    commit_sha: str
    parse_status: ParseStatus
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContentChunk:
    repository_id: uuid.UUID
    index_version: int
    ordinal: int
    file_path: str
    language: str
    chunk_type: ChunkType
    symbol_name: str | None
    qualified_name: str | None
    parent_qualified_name: str | None
    heading_hierarchy: tuple[str, ...]
    imports: tuple[str, ...]
    decorators: tuple[str, ...]
    signature: str | None
    docstring: str | None
    start_line: int
    end_line: int
    commit_sha: str
    content_hash: str
    content: str


@dataclass(frozen=True, slots=True)
class SymbolRecord:
    file_path: str
    language: str
    symbol_type: SymbolType
    symbol_name: str
    qualified_name: str
    start_line: int
    end_line: int
    content_hash: str
    commit_sha: str


@dataclass(frozen=True, slots=True)
class ChunkFingerprint:
    ordinal: int
    file_path: str
    chunk_type: ChunkType
    qualified_name: str | None
    start_line: int
    end_line: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    repository_id: uuid.UUID
    index_version: int
    commit_sha: str
    parsed_file_count: int
    partial_file_count: int
    skipped_file_count: int
    symbol_count: int
    chunk_count: int
    warning_counts: dict[str, int]
    symbols: tuple[SymbolRecord, ...]
    chunk_fingerprints: tuple[ChunkFingerprint, ...]
    chunks: tuple[ContentChunk, ...]
