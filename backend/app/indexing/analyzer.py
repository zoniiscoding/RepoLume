"""Bounded repository parsing and chunking isolated from the worker process."""

import asyncio
import multiprocessing
import os
import resource
import stat
import sys
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from multiprocessing.connection import Connection
from pathlib import Path, PurePosixPath
from typing import Protocol

from app.core.config import Settings
from app.indexing.call_graph import PythonCallGraphBuilder
from app.indexing.chunking import DocumentationChunker, PythonChunker
from app.indexing.discovery import DiscoveryResult
from app.indexing.failures import IndexingError
from app.indexing.models import (
    ChunkFingerprint,
    ContentChunk,
    ParsedFile,
    ParseStatus,
    ProcessingResult,
    SymbolRecord,
)
from app.indexing.python_parser import PythonStaticParser


@dataclass(frozen=True, slots=True)
class ParserLimits:
    max_input_bytes: int
    max_symbols_per_file: int
    max_symbol_bytes: int
    max_chunk_bytes: int
    max_chunks_per_file: int
    max_total_chunks: int
    max_total_chunk_bytes: int
    max_document_section_bytes: int
    max_warnings_per_file: int
    max_call_sites_per_file: int
    max_total_call_sites: int
    max_call_expression_bytes: int
    timeout_seconds: float
    process_memory_bytes: int
    process_cpu_seconds: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "ParserLimits":
        return cls(
            max_input_bytes=settings.parser_max_input_bytes,
            max_symbols_per_file=settings.parser_max_symbols_per_file,
            max_symbol_bytes=settings.parser_max_symbol_bytes,
            max_chunk_bytes=settings.parser_max_chunk_bytes,
            max_chunks_per_file=settings.parser_max_chunks_per_file,
            max_total_chunks=settings.parser_max_total_chunks,
            max_total_chunk_bytes=settings.parser_max_total_chunk_bytes,
            max_document_section_bytes=settings.parser_max_document_section_bytes,
            max_warnings_per_file=settings.parser_max_warnings_per_file,
            max_call_sites_per_file=settings.parser_max_call_sites_per_file,
            max_total_call_sites=settings.parser_max_total_call_sites,
            max_call_expression_bytes=settings.parser_max_call_expression_bytes,
            timeout_seconds=settings.parser_timeout_seconds,
            process_memory_bytes=settings.parser_process_memory_bytes,
            process_cpu_seconds=settings.parser_process_cpu_seconds,
        )


@dataclass(frozen=True, slots=True)
class _ScanResult:
    parsed_files: tuple[ParsedFile, ...]
    documentation: tuple[tuple[str, str], ...]
    parsed_count: int
    partial_count: int
    skipped_count: int
    warning_counts: Counter[str]


class RepositoryAnalyzerProtocol(Protocol):
    async def analyze(
        self,
        *,
        checkout: Path,
        discovery: DiscoveryResult,
        repository_id: uuid.UUID,
        index_version: int,
        commit_sha: str,
        on_chunking: Callable[[], Awaitable[None]],
        on_graphing: Callable[[], Awaitable[None]] | None = None,
    ) -> ProcessingResult: ...


class RepositoryAnalyzer:
    """Synchronously parse trusted discovery output without executing source."""

    def __init__(self, limits: ParserLimits) -> None:
        self._limits = limits
        self._python_parser = PythonStaticParser(
            max_symbols=limits.max_symbols_per_file,
            max_warnings=limits.max_warnings_per_file,
            max_call_sites=limits.max_call_sites_per_file,
            max_call_expression_bytes=limits.max_call_expression_bytes,
        )
        self._python_chunker = PythonChunker(
            max_symbol_bytes=limits.max_symbol_bytes,
            max_chunk_bytes=limits.max_chunk_bytes,
            max_chunks_per_file=limits.max_chunks_per_file,
            max_warnings=limits.max_warnings_per_file,
        )
        self._documentation_chunker = DocumentationChunker(
            max_chunk_bytes=limits.max_chunk_bytes,
            max_section_bytes=limits.max_document_section_bytes,
            max_chunks_per_file=limits.max_chunks_per_file,
            max_warnings=limits.max_warnings_per_file,
        )

    def analyze(
        self,
        *,
        checkout: Path,
        discovery: DiscoveryResult,
        repository_id: uuid.UUID,
        index_version: int,
        commit_sha: str,
        on_chunking: Callable[[], None] | None = None,
        on_graphing: Callable[[], None] | None = None,
    ) -> ProcessingResult:
        root = checkout.resolve(strict=True)
        if not root.is_dir():
            raise self._unsafe_path()
        scan = self._scan_files(root, discovery, commit_sha)
        if on_chunking is not None:
            on_chunking()
        symbols, chunks, warnings = self._build_chunks(
            scan=scan,
            repository_id=repository_id,
            index_version=index_version,
            commit_sha=commit_sha,
        )
        symbols.sort(
            key=lambda item: (
                item.file_path,
                item.start_line,
                item.end_line,
                item.qualified_name,
                item.content_hash,
            )
        )
        chunks.sort(
            key=lambda item: (
                item.file_path,
                item.start_line,
                item.end_line,
                item.chunk_type.value,
                item.qualified_name or "",
                item.content_hash,
            )
        )
        chunks = [replace(chunk, ordinal=ordinal) for ordinal, chunk in enumerate(chunks)]
        self._assert_total_chunk_bytes(chunks)
        if on_graphing is not None:
            on_graphing()
        graph = PythonCallGraphBuilder(
            max_total_call_sites=self._limits.max_total_call_sites
        ).build(
            repository_id=repository_id,
            index_version=index_version,
            commit_sha=commit_sha,
            parsed_files=scan.parsed_files,
            symbols=tuple(symbols),
        )
        if graph.warning_count:
            warnings["call_graph_resolution_warning"] += graph.warning_count
        fingerprints = tuple(
            ChunkFingerprint(
                ordinal=chunk.ordinal,
                file_path=chunk.file_path,
                chunk_type=chunk.chunk_type,
                qualified_name=chunk.qualified_name,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                content_hash=chunk.content_hash,
            )
            for chunk in chunks
        )
        return ProcessingResult(
            repository_id=repository_id,
            index_version=index_version,
            commit_sha=commit_sha,
            parsed_file_count=scan.parsed_count,
            partial_file_count=scan.partial_count,
            skipped_file_count=scan.skipped_count,
            symbol_count=len(symbols),
            chunk_count=len(chunks),
            warning_counts=dict(sorted(warnings.items())),
            symbols=tuple(symbols),
            chunk_fingerprints=fingerprints,
            chunks=tuple(chunks),
            call_site_count=graph.call_site_count,
            exact_edge_count=graph.exact_edge_count,
            ambiguous_edge_count=graph.ambiguous_edge_count,
            unresolved_call_count=graph.unresolved_call_count,
            graph_warning_count=graph.warning_count,
            graph_fingerprint=graph.fingerprint,
            call_edges=graph.edges,
        )

    def _build_chunks(
        self,
        *,
        scan: _ScanResult,
        repository_id: uuid.UUID,
        index_version: int,
        commit_sha: str,
    ) -> tuple[list[SymbolRecord], list[ContentChunk], Counter[str]]:
        symbols: list[SymbolRecord] = []
        chunks: list[ContentChunk] = []
        warnings = scan.warning_counts.copy()
        for parsed in scan.parsed_files:
            symbols.extend(
                SymbolRecord(
                    file_path=symbol.file_path,
                    language=symbol.language,
                    symbol_type=symbol.symbol_type,
                    symbol_name=symbol.name,
                    qualified_name=symbol.qualified_name,
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    content_hash=symbol.content_hash,
                    commit_sha=symbol.commit_sha,
                )
                for symbol in parsed.symbols
                if len(symbol.source_text.encode("utf-8")) <= self._limits.max_symbol_bytes
            )
            result = self._python_chunker.chunk(
                parsed,
                repository_id=repository_id,
                index_version=index_version,
            )
            warnings.update(result.warnings)
            chunks.extend(result.chunks)
            self._assert_total_chunks(len(chunks))
        for file_path, source_text in scan.documentation:
            result = self._documentation_chunker.chunk(
                file_path=file_path,
                source_text=source_text,
                repository_id=repository_id,
                index_version=index_version,
                commit_sha=commit_sha,
            )
            warnings.update(result.warnings)
            chunks.extend(result.chunks)
            self._assert_total_chunks(len(chunks))
        return symbols, chunks, warnings

    def _scan_files(
        self,
        root: Path,
        discovery: DiscoveryResult,
        commit_sha: str,
    ) -> _ScanResult:
        warnings: Counter[str] = Counter()
        parsed_files: list[ParsedFile] = []
        documentation: list[tuple[str, str]] = []
        parsed_count = 0
        partial_count = 0
        skipped_count = 0
        for discovered in discovery.files:
            suffix = PurePosixPath(discovered.relative_path).suffix.lower()
            try:
                source_text = self._read_source(
                    root, discovered.relative_path, discovered.size_bytes
                )
            except _SkippedFileError as skipped:
                warnings[skipped.code] += 1
                skipped_count += 1
                continue
            if suffix in {".py", ".pyi"}:
                try:
                    parsed = self._python_parser.parse(
                        file_path=discovered.relative_path,
                        source_text=source_text,
                        commit_sha=commit_sha,
                    )
                except Exception:  # noqa: BLE001 -- parser internals remain private
                    warnings["internal_parser_failure"] += 1
                    skipped_count += 1
                    continue
                warnings.update(parsed.warnings)
                if parsed.parse_status is ParseStatus.COMPLETE:
                    parsed_count += 1
                    parsed_files.append(parsed)
                elif parsed.parse_status is ParseStatus.PARTIAL:
                    partial_count += 1
                    parsed_files.append(parsed)
                else:
                    skipped_count += 1
            elif suffix in {".md", ".markdown", ".txt", ".rst"}:
                documentation.append((discovered.relative_path, source_text))
                parsed_count += 1
            else:
                warnings["unsupported_file"] += 1
                skipped_count += 1
        return _ScanResult(
            parsed_files=tuple(parsed_files),
            documentation=tuple(documentation),
            parsed_count=parsed_count,
            partial_count=partial_count,
            skipped_count=skipped_count,
            warning_counts=warnings,
        )

    def _read_source(self, root: Path, relative_path: str, expected_size: int) -> str:
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise self._unsafe_path()
        candidate = root.joinpath(*relative.parts)
        try:
            if candidate.is_symlink():
                raise self._unsafe_path()
            resolved = candidate.resolve(strict=True)
            file_stat = resolved.stat()
        except (OSError, RuntimeError) as error:
            raise self._unsafe_path() from error
        if not resolved.is_relative_to(root) or not stat.S_ISREG(file_stat.st_mode):
            raise self._unsafe_path()
        if file_stat.st_size != expected_size:
            raise IndexingError(
                code="repository_changed_during_processing",
                message="Repository contents changed during static processing",
                retryable=False,
            )
        if file_stat.st_size > self._limits.max_input_bytes:
            raise _SkippedFileError("parser_input_too_large")
        try:
            with resolved.open("rb") as source_file:
                raw = source_file.read(self._limits.max_input_bytes + 1)
        except OSError as error:
            raise IndexingError(
                code="parser_filesystem_error",
                message="Repository files could not be read for static processing",
                retryable=False,
            ) from error
        if len(raw) > self._limits.max_input_bytes:
            raise _SkippedFileError("parser_input_too_large")
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise _SkippedFileError("unsupported_encoding") from error

    def _assert_total_chunks(self, chunk_count: int) -> None:
        if chunk_count > self._limits.max_total_chunks:
            raise IndexingError(
                code="chunk_count_exceeded",
                message="Repository exceeds the configured chunk-count limit",
                retryable=False,
            )

    def _assert_total_chunk_bytes(self, chunks: list[ContentChunk]) -> None:
        total_bytes = sum(len(chunk.content.encode("utf-8")) for chunk in chunks)
        if total_bytes > self._limits.max_total_chunk_bytes:
            raise IndexingError(
                code="chunk_bytes_exceeded",
                message="Repository exceeds the configured chunk-content limit",
                retryable=False,
            )

    @staticmethod
    def _unsafe_path() -> IndexingError:
        return IndexingError(
            code="unsafe_repository_path",
            message="Repository contains an unsafe path",
            retryable=False,
        )


class ProcessIsolatedAnalyzer:
    """Run static processing in a killable, resource-bounded child process."""

    def __init__(self, settings: Settings | ParserLimits) -> None:
        self._limits = (
            settings if isinstance(settings, ParserLimits) else ParserLimits.from_settings(settings)
        )

    async def analyze(  # noqa: PLR0912 -- explicit child-process protocol fails closed
        self,
        *,
        checkout: Path,
        discovery: DiscoveryResult,
        repository_id: uuid.UUID,
        index_version: int,
        commit_sha: str,
        on_chunking: Callable[[], Awaitable[None]],
        on_graphing: Callable[[], Awaitable[None]] | None = None,
    ) -> ProcessingResult:
        context = multiprocessing.get_context("spawn")
        receiving, sending = context.Pipe(duplex=False)
        process = context.Process(
            target=_analyzer_process,
            args=(
                sending,
                self._limits,
                checkout,
                discovery,
                repository_id,
                index_version,
                commit_sha,
            ),
            name="repolume-static-parser",
        )
        process.start()
        sending.close()
        deadline = time.monotonic() + self._limits.timeout_seconds
        try:
            while time.monotonic() < deadline:
                if receiving.poll():
                    try:
                        kind, payload = receiving.recv()
                    except EOFError as error:
                        raise IndexingError(
                            code="internal_parser_failure",
                            message="Static repository processing failed safely",
                            retryable=False,
                        ) from error
                    if kind == "stage":
                        if payload == "chunking":
                            await on_chunking()
                        elif payload == "graphing" and on_graphing is not None:
                            await on_graphing()
                        continue
                    if kind == "result":
                        if (
                            isinstance(payload, ProcessingResult)
                            and payload.repository_id == repository_id
                            and payload.index_version == index_version
                            and payload.commit_sha == commit_sha
                        ):
                            return payload
                        raise IndexingError(
                            code="internal_parser_failure",
                            message="Static repository processing failed safely",
                            retryable=False,
                        )
                    if kind == "error":
                        code, message, retryable = payload
                        raise IndexingError(code=code, message=message, retryable=retryable)
                if not process.is_alive():
                    raise IndexingError(
                        code="internal_parser_failure",
                        message="Static repository processing failed safely",
                        retryable=False,
                    )
                await asyncio.sleep(0.02)
            raise IndexingError(
                code="parser_timeout",
                message="Static repository processing exceeded its time limit",
                retryable=False,
            )
        finally:
            receiving.close()
            if process.is_alive():
                process.terminate()
            await asyncio.to_thread(process.join, 5)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 5)


class _SkippedFileError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _analyzer_process(
    connection: Connection,
    limits: ParserLimits,
    checkout: Path,
    discovery: DiscoveryResult,
    repository_id: uuid.UUID,
    index_version: int,
    commit_sha: str,
) -> None:
    try:
        _apply_process_limits(limits)
        result = RepositoryAnalyzer(limits).analyze(
            checkout=checkout,
            discovery=discovery,
            repository_id=repository_id,
            index_version=index_version,
            commit_sha=commit_sha,
            on_chunking=lambda: connection.send(("stage", "chunking")),
            on_graphing=lambda: connection.send(("stage", "graphing")),
        )
        connection.send(("result", result))
    except IndexingError as error:
        connection.send(("error", (error.code, error.safe_message, error.retryable)))
    except BaseException:  # noqa: BLE001 -- never serialize repository-triggered internals
        connection.send(
            (
                "error",
                (
                    "internal_parser_failure",
                    "Static repository processing failed safely",
                    False,
                ),
            )
        )
    finally:
        connection.close()


def _apply_process_limits(limits: ParserLimits) -> None:
    if os.name != "posix":
        return
    resource.setrlimit(
        resource.RLIMIT_CPU,
        (limits.process_cpu_seconds, limits.process_cpu_seconds),
    )
    if sys.platform != "darwin":
        resource.setrlimit(
            resource.RLIMIT_AS,
            (limits.process_memory_bytes, limits.process_memory_bytes),
        )
