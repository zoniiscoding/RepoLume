"""Deterministic AST-aware Python and heading-aware documentation chunking."""

import re
import uuid
from dataclasses import dataclass, replace
from pathlib import PurePosixPath

from app.db.models.enums import SymbolType
from app.indexing.models import (
    ChunkType,
    ContentChunk,
    ParsedFile,
    ParsedSymbol,
    SourceSegment,
)
from app.indexing.python_parser import content_hash

DEFINITION_NODE_TYPES = frozenset(
    {"function_definition", "class_definition", "decorated_definition"}
)
MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
DOCUMENTATION_SUFFIXES = frozenset({".txt", ".rst"})
HEADING_PATTERN = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE_PATTERN = re.compile(r"^[ \t]*(`{3,}|~{3,})")


@dataclass(frozen=True, slots=True)
class FileChunkResult:
    chunks: tuple[ContentChunk, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _DocumentationSection:
    start_line: int
    end_line: int
    heading_hierarchy: tuple[str, ...]


class PythonChunker:
    """Chunk Python at definitions and immediate statement boundaries."""

    def __init__(
        self,
        *,
        max_symbol_bytes: int,
        max_chunk_bytes: int,
        max_chunks_per_file: int,
        max_warnings: int,
    ) -> None:
        self._max_symbol_bytes = max_symbol_bytes
        self._max_chunk_bytes = max_chunk_bytes
        self._max_chunks_per_file = max_chunks_per_file
        self._max_warnings = max_warnings

    def chunk(
        self,
        parsed: ParsedFile,
        *,
        repository_id: uuid.UUID,
        index_version: int,
    ) -> FileChunkResult:
        chunks: list[ContentChunk] = []
        warnings: list[str] = []
        lines = parsed.source_text.splitlines()
        imports = tuple(item.source_text for item in parsed.imports)
        for symbol in parsed.symbols:
            if len(symbol.source_text.encode("utf-8")) > self._max_symbol_bytes:
                warnings.append("symbol_too_large")
                continue
            symbol_chunks = self._symbol_chunks(
                parsed=parsed,
                symbol=symbol,
                lines=lines,
                repository_id=repository_id,
                index_version=index_version,
                imports=imports,
            )
            if symbol_chunks is None:
                warnings.append("symbol_too_large")
                continue
            chunks.extend(symbol_chunks)
        for run in self._contiguous_runs(parsed.module_segments):
            module_chunks = self._segment_chunks(
                parsed=parsed,
                segments=run,
                lines=lines,
                repository_id=repository_id,
                index_version=index_version,
                chunk_type=ChunkType.MODULE,
                symbol=None,
                imports=imports,
            )
            if module_chunks is None:
                warnings.append("module_code_too_large")
                break
            chunks.extend(module_chunks)
        chunks.sort(
            key=lambda item: (
                item.start_line,
                item.end_line,
                item.chunk_type.value,
                item.qualified_name or "",
                item.content_hash,
            )
        )
        if len(chunks) > self._max_chunks_per_file:
            return FileChunkResult(chunks=(), warnings=("chunk_count_exceeded",))
        chunks = [self._with_ordinal(item, index) for index, item in enumerate(chunks)]
        return FileChunkResult(
            chunks=tuple(chunks),
            warnings=tuple(warnings[: self._max_warnings]),
        )

    def _symbol_chunks(
        self,
        *,
        parsed: ParsedFile,
        symbol: ParsedSymbol,
        lines: list[str],
        repository_id: uuid.UUID,
        index_version: int,
        imports: tuple[str, ...],
    ) -> list[ContentChunk] | None:
        if symbol.symbol_type is SymbolType.CLASS:
            header = self._make_chunk(
                parsed=parsed,
                repository_id=repository_id,
                index_version=index_version,
                chunk_type=ChunkType.CLASS_OVERVIEW,
                symbol=symbol,
                imports=imports,
                content=symbol.header_text,
                start_line=symbol.header_start_line,
                end_line=symbol.header_end_line,
            )
            if header is None:
                return None
            overview_segments = tuple(
                item for item in symbol.body_segments if item.node_type not in DEFINITION_NODE_TYPES
            )
            overview: list[ContentChunk] = []
            for run in self._contiguous_runs(overview_segments):
                run_chunks = self._segment_chunks(
                    parsed=parsed,
                    segments=run,
                    lines=lines,
                    repository_id=repository_id,
                    index_version=index_version,
                    chunk_type=ChunkType.CLASS_OVERVIEW,
                    symbol=symbol,
                    imports=imports,
                )
                if run_chunks is None:
                    return None
                overview.extend(run_chunks)
            return [header, *overview]
        if len(symbol.source_text.encode("utf-8")) <= self._max_chunk_bytes:
            chunk = self._make_chunk(
                parsed=parsed,
                repository_id=repository_id,
                index_version=index_version,
                chunk_type=self._chunk_type(symbol),
                symbol=symbol,
                imports=imports,
                content=symbol.source_text,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
            )
            return None if chunk is None else [chunk]
        body_segments = tuple(
            item for item in symbol.body_segments if item.node_type not in DEFINITION_NODE_TYPES
        )
        chunks: list[ContentChunk] = []
        for run in self._contiguous_runs(body_segments):
            run_chunks = self._segment_chunks(
                parsed=parsed,
                segments=run,
                lines=lines,
                repository_id=repository_id,
                index_version=index_version,
                chunk_type=self._chunk_type(symbol),
                symbol=symbol,
                imports=imports,
            )
            if run_chunks is None:
                return None
            chunks.extend(run_chunks)
        return chunks

    def _segment_chunks(
        self,
        *,
        parsed: ParsedFile,
        segments: tuple[SourceSegment, ...],
        lines: list[str],
        repository_id: uuid.UUID,
        index_version: int,
        chunk_type: ChunkType,
        symbol: ParsedSymbol | None,
        imports: tuple[str, ...],
    ) -> list[ContentChunk] | None:
        if not segments:
            return []
        chunks: list[ContentChunk] = []
        group_start = segments[0].start_line
        group_end = segments[0].end_line
        for segment in (*segments[1:], None):
            candidate_end = group_end if segment is None else segment.end_line
            candidate = self._line_slice(lines, group_start, candidate_end)
            if segment is not None and len(candidate.encode("utf-8")) <= self._max_chunk_bytes:
                group_end = segment.end_line
                continue
            content = self._line_slice(lines, group_start, group_end)
            chunk = self._make_chunk(
                parsed=parsed,
                repository_id=repository_id,
                index_version=index_version,
                chunk_type=chunk_type,
                symbol=symbol,
                imports=imports,
                content=content,
                start_line=group_start,
                end_line=group_end,
            )
            if chunk is None:
                return None
            chunks.append(chunk)
            if segment is not None:
                group_start = segment.start_line
                group_end = segment.end_line
        return chunks

    def _make_chunk(
        self,
        *,
        parsed: ParsedFile,
        repository_id: uuid.UUID,
        index_version: int,
        chunk_type: ChunkType,
        symbol: ParsedSymbol | None,
        imports: tuple[str, ...],
        content: str,
        start_line: int,
        end_line: int,
    ) -> ContentChunk | None:
        if not content or len(content.encode("utf-8")) > self._max_chunk_bytes:
            return None
        return ContentChunk(
            repository_id=repository_id,
            index_version=index_version,
            ordinal=0,
            file_path=parsed.file_path,
            language="python",
            chunk_type=chunk_type,
            symbol_name=None if symbol is None else symbol.name,
            qualified_name=parsed.module_name if symbol is None else symbol.qualified_name,
            parent_qualified_name=None if symbol is None else symbol.parent_qualified_name,
            heading_hierarchy=(),
            imports=imports,
            decorators=() if symbol is None else symbol.decorators,
            signature=None if symbol is None else symbol.signature,
            docstring=parsed.module_docstring if symbol is None else symbol.docstring,
            start_line=start_line,
            end_line=end_line,
            commit_sha=parsed.commit_sha,
            content_hash=content_hash(content),
            content=content,
        )

    @staticmethod
    def _chunk_type(symbol: ParsedSymbol) -> ChunkType:
        if symbol.symbol_type is SymbolType.ASYNC_FUNCTION:
            return ChunkType.ASYNC_FUNCTION
        if symbol.symbol_type is SymbolType.METHOD:
            return ChunkType.METHOD
        return ChunkType.FUNCTION

    @staticmethod
    def _line_slice(lines: list[str], start_line: int, end_line: int) -> str:
        return "\n".join(lines[start_line - 1 : end_line])

    @staticmethod
    def _with_ordinal(chunk: ContentChunk, ordinal: int) -> ContentChunk:
        return replace(chunk, ordinal=ordinal)

    @staticmethod
    def _contiguous_runs(
        segments: tuple[SourceSegment, ...],
    ) -> tuple[tuple[SourceSegment, ...], ...]:
        """Keep line slices from crossing excluded definitions or imports."""

        if not segments:
            return ()
        runs: list[list[SourceSegment]] = [[segments[0]]]
        for segment in segments[1:]:
            if segment.start_line > runs[-1][-1].end_line + 1:
                runs.append([segment])
            else:
                runs[-1].append(segment)
        return tuple(tuple(run) for run in runs)


class DocumentationChunker:
    """Chunk Markdown by headings/paragraphs and plain text by paragraphs."""

    def __init__(
        self,
        *,
        max_chunk_bytes: int,
        max_section_bytes: int,
        max_chunks_per_file: int,
        max_warnings: int,
    ) -> None:
        self._max_chunk_bytes = max_chunk_bytes
        self._max_section_bytes = max_section_bytes
        self._max_chunks_per_file = max_chunks_per_file
        self._max_warnings = max_warnings

    def chunk(
        self,
        *,
        file_path: str,
        source_text: str,
        repository_id: uuid.UUID,
        index_version: int,
        commit_sha: str,
    ) -> FileChunkResult:
        normalized = source_text.replace("\r\n", "\n").replace("\r", "\n")
        suffix = PurePosixPath(file_path).suffix.lower()
        if suffix not in MARKDOWN_SUFFIXES | DOCUMENTATION_SUFFIXES:
            return FileChunkResult(chunks=(), warnings=("unsupported_file",))
        lines = normalized.splitlines()
        sections = (
            self._markdown_sections(lines)
            if suffix in MARKDOWN_SUFFIXES
            else (_DocumentationSection(1, max(1, len(lines)), ()),)
        )
        chunks: list[ContentChunk] = []
        warnings: list[str] = []
        chunk_type = ChunkType.MARKDOWN if suffix in MARKDOWN_SUFFIXES else ChunkType.DOCUMENTATION
        for section in sections:
            section_text = self._line_slice(lines, section.start_line, section.end_line)
            if len(section_text.encode("utf-8")) > self._max_section_bytes:
                warnings.append("documentation_section_too_large")
                continue
            ranges = self._paragraph_ranges(lines, section.start_line, section.end_line)
            packed = self._pack_ranges(lines, ranges)
            if packed is None:
                warnings.append("documentation_section_too_large")
                continue
            for start_line, end_line in packed:
                content = self._line_slice(lines, start_line, end_line)
                if not content:
                    continue
                chunks.append(
                    ContentChunk(
                        repository_id=repository_id,
                        index_version=index_version,
                        ordinal=0,
                        file_path=file_path,
                        language="markdown" if suffix in MARKDOWN_SUFFIXES else "text",
                        chunk_type=chunk_type,
                        symbol_name=None,
                        qualified_name=None,
                        parent_qualified_name=None,
                        heading_hierarchy=section.heading_hierarchy,
                        imports=(),
                        decorators=(),
                        signature=None,
                        docstring=None,
                        start_line=start_line,
                        end_line=end_line,
                        commit_sha=commit_sha,
                        content_hash=content_hash(content),
                        content=content,
                    )
                )
        if len(chunks) > self._max_chunks_per_file:
            return FileChunkResult(chunks=(), warnings=("chunk_count_exceeded",))
        chunks.sort(key=lambda item: (item.start_line, item.end_line, item.content_hash))
        chunks = [PythonChunker._with_ordinal(item, index) for index, item in enumerate(chunks)]
        return FileChunkResult(
            chunks=tuple(chunks),
            warnings=tuple(warnings[: self._max_warnings]),
        )

    @staticmethod
    def _markdown_sections(lines: list[str]) -> tuple[_DocumentationSection, ...]:
        if not lines:
            return (_DocumentationSection(1, 1, ()),)
        starts: list[tuple[int, tuple[str, ...]]] = [(1, ())]
        hierarchy: list[str] = []
        fence: str | None = None
        for index, line in enumerate(lines, start=1):
            fence_match = FENCE_PATTERN.match(line)
            if fence_match:
                marker = fence_match.group(1)[0]
                fence = None if fence == marker else marker if fence is None else fence
                continue
            if fence is not None:
                continue
            heading = HEADING_PATTERN.match(line)
            if heading is None:
                continue
            level = len(heading.group(1))
            title = heading.group(2).strip()
            hierarchy = hierarchy[: level - 1]
            hierarchy.append(title)
            if index == 1 and starts == [(1, ())]:
                starts[0] = (1, tuple(hierarchy))
            else:
                starts.append((index, tuple(hierarchy)))
        sections: list[_DocumentationSection] = []
        for position, (start, headings) in enumerate(starts):
            end = starts[position + 1][0] - 1 if position + 1 < len(starts) else len(lines)
            if end >= start:
                sections.append(_DocumentationSection(start, end, headings))
        return tuple(sections)

    @staticmethod
    def _paragraph_ranges(
        lines: list[str], start_line: int, end_line: int
    ) -> tuple[tuple[int, int], ...]:
        ranges: list[tuple[int, int]] = []
        paragraph_start: int | None = None
        fence: str | None = None
        for line_number in range(start_line, end_line + 1):
            line = lines[line_number - 1] if line_number <= len(lines) else ""
            fence_match = FENCE_PATTERN.match(line)
            if fence_match:
                marker = fence_match.group(1)[0]
                if paragraph_start is None:
                    paragraph_start = line_number
                fence = None if fence == marker else marker if fence is None else fence
                continue
            if not line.strip() and fence is None:
                if paragraph_start is not None:
                    ranges.append((paragraph_start, line_number - 1))
                    paragraph_start = None
                continue
            if paragraph_start is None:
                paragraph_start = line_number
        if paragraph_start is not None:
            ranges.append((paragraph_start, end_line))
        return tuple(ranges)

    def _pack_ranges(
        self, lines: list[str], ranges: tuple[tuple[int, int], ...]
    ) -> list[tuple[int, int]] | None:
        if not ranges:
            return []
        output: list[tuple[int, int]] = []
        start, end = ranges[0]
        pending: tuple[tuple[int, int] | None, ...] = (*ranges[1:], None)
        for candidate in pending:
            next_end = end if candidate is None else candidate[1]
            content = self._line_slice(lines, start, next_end)
            if candidate is not None and len(content.encode("utf-8")) <= self._max_chunk_bytes:
                end = candidate[1]
                continue
            current = self._line_slice(lines, start, end)
            if len(current.encode("utf-8")) > self._max_chunk_bytes:
                split = self._split_lines(lines, start, end)
                if split is None:
                    return None
                output.extend(split)
            else:
                output.append((start, end))
            if candidate is not None:
                start, end = candidate
        return output

    def _split_lines(
        self, lines: list[str], start_line: int, end_line: int
    ) -> list[tuple[int, int]] | None:
        output: list[tuple[int, int]] = []
        start = start_line
        end = start_line
        for line_number in range(start_line, end_line + 1):
            single = self._line_slice(lines, line_number, line_number)
            if len(single.encode("utf-8")) > self._max_chunk_bytes:
                return None
            candidate = self._line_slice(lines, start, line_number)
            if len(candidate.encode("utf-8")) > self._max_chunk_bytes:
                output.append((start, end))
                start = line_number
            end = line_number
        output.append((start, end))
        return output

    @staticmethod
    def _line_slice(lines: list[str], start_line: int, end_line: int) -> str:
        return "\n".join(lines[start_line - 1 : end_line])
