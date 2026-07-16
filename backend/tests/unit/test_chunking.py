"""AST-aware Python and heading-aware documentation chunk construction."""

import uuid
from pathlib import Path

from app.indexing.chunking import DocumentationChunker, PythonChunker
from app.indexing.models import ChunkType, ParsedFile
from app.indexing.python_parser import PythonStaticParser

FIXTURE = Path(__file__).parents[1] / "fixtures" / "milestone4_repository"
REPOSITORY_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
COMMIT = "b" * 40


def parse(source: str, path: str = "module.py") -> ParsedFile:
    return PythonStaticParser(max_symbols=100, max_warnings=10).parse(
        file_path=path,
        source_text=source,
        commit_sha=COMMIT,
    )


def python_chunker(*, max_chunk_bytes: int = 512, max_symbol_bytes: int = 4096) -> PythonChunker:
    return PythonChunker(
        max_symbol_bytes=max_symbol_bytes,
        max_chunk_bytes=max_chunk_bytes,
        max_chunks_per_file=100,
        max_warnings=10,
    )


def documentation_chunker(
    *, max_chunk_bytes: int = 512, max_section_bytes: int = 4096, max_chunks: int = 100
) -> DocumentationChunker:
    return DocumentationChunker(
        max_chunk_bytes=max_chunk_bytes,
        max_section_bytes=max_section_bytes,
        max_chunks_per_file=max_chunks,
        max_warnings=10,
    )


def test_small_functions_classes_methods_and_module_code_are_ast_aware() -> None:
    source = (FIXTURE / "src/package/module.py").read_text()
    result = python_chunker(max_chunk_bytes=2048).chunk(
        parse(source, "src/package/module.py"),
        repository_id=REPOSITORY_ID,
        index_version=4,
    )

    assert result.warnings == ()
    assert [chunk.ordinal for chunk in result.chunks] == list(range(len(result.chunks)))
    assert any(
        chunk.chunk_type is ChunkType.ASYNC_FUNCTION
        and chunk.qualified_name == "src.package.module.fetch_value"
        and chunk.start_line == 14
        and chunk.end_line == 29
        for chunk in result.chunks
    )
    assert any(
        chunk.chunk_type is ChunkType.CLASS_OVERVIEW
        and chunk.qualified_name == "src.package.module.Container"
        and chunk.start_line == 32
        for chunk in result.chunks
    )
    assert any(chunk.chunk_type is ChunkType.MODULE for chunk in result.chunks)
    assert all(chunk.repository_id == REPOSITORY_ID for chunk in result.chunks)
    assert all(chunk.index_version == 4 for chunk in result.chunks)


def test_large_function_splits_only_at_statement_boundaries_without_truncation() -> None:
    source = (
        "def large(value: int) -> int:\n"
        "    first = value + 1\n"
        "    second = first + 2\n"
        "    third = second + 3\n"
        "    return third\n"
    )
    result = python_chunker(max_chunk_bytes=45).chunk(
        parse(source), repository_id=REPOSITORY_ID, index_version=1
    )

    chunks = [item for item in result.chunks if item.chunk_type is ChunkType.FUNCTION]
    assert [(item.start_line, item.end_line) for item in chunks] == [(2, 3), (4, 5)]
    assert "".join(item.content.replace("\n", "") for item in chunks) == (
        "    first = value + 1    second = first + 2    third = second + 3    return third"
    )


def test_nested_definition_is_not_reintroduced_by_a_broad_line_slice() -> None:
    source = (
        "def outer():\n"
        "    before = 1\n"
        "    def inner():\n"
        "        return 2\n"
        "    after = 3\n"
        "    return before + after\n"
    )
    result = python_chunker(max_chunk_bytes=35).chunk(
        parse(source), repository_id=REPOSITORY_ID, index_version=1
    )
    outer = [item for item in result.chunks if item.qualified_name == "module.outer"]

    assert all("def inner" not in item.content for item in outer)
    assert [(item.start_line, item.end_line) for item in outer] == [
        (2, 2),
        (5, 5),
        (6, 6),
    ]


def test_large_class_overview_stays_separate_from_method_chunks() -> None:
    source = (
        "class LargeContainer:\n"
        "    first_value = 'aaaaaaaaaaaaaaaaaaaa'\n"
        "    second_value = 'bbbbbbbbbbbbbbbbbbbb'\n"
        "    def method(self) -> str:\n"
        "        return self.first_value\n"
    )
    result = python_chunker(max_chunk_bytes=60).chunk(
        parse(source), repository_id=REPOSITORY_ID, index_version=1
    )
    overview = [item for item in result.chunks if item.chunk_type is ChunkType.CLASS_OVERVIEW]
    methods = [item for item in result.chunks if item.chunk_type is ChunkType.METHOD]

    assert [(item.start_line, item.end_line) for item in overview] == [
        (1, 1),
        (2, 2),
        (3, 3),
    ]
    assert [(item.start_line, item.end_line) for item in methods] == [(4, 5)]
    assert all("def method" not in item.content for item in overview)


def test_oversized_symbol_and_chunk_count_have_safe_classifications() -> None:
    parsed = parse("def large():\n    value = '" + ("x" * 200) + "'\n")
    oversized = python_chunker(max_chunk_bytes=64, max_symbol_bytes=100).chunk(
        parsed, repository_id=REPOSITORY_ID, index_version=1
    )
    count_limited = PythonChunker(
        max_symbol_bytes=4096,
        max_chunk_bytes=32,
        max_chunks_per_file=1,
        max_warnings=10,
    ).chunk(
        parse("def one():\n    return 1\n\ndef two():\n    return 2\n"),
        repository_id=REPOSITORY_ID,
        index_version=1,
    )

    assert oversized.chunks == ()
    assert oversized.warnings == ("symbol_too_large",)
    assert count_limited.chunks == ()
    assert count_limited.warnings == ("chunk_count_exceeded",)


def test_markdown_headings_fences_and_prompt_text_are_preserved_inert() -> None:
    source = (FIXTURE / "docs/guide.md").read_text()
    first = documentation_chunker(max_chunk_bytes=1024).chunk(
        file_path="docs/guide.md",
        source_text=source,
        repository_id=REPOSITORY_ID,
        index_version=2,
        commit_sha=COMMIT,
    )
    second = documentation_chunker(max_chunk_bytes=1024).chunk(
        file_path="docs/guide.md",
        source_text=source,
        repository_id=REPOSITORY_ID,
        index_version=2,
        commit_sha=COMMIT,
    )

    assert first == second
    assert [item.heading_hierarchy for item in first.chunks] == [
        ("Guide",),
        ("Guide", "Usage"),
        ("Guide", "Usage", "Details"),
    ]
    assert first.chunks[0].start_line == 1
    assert first.chunks[-1].end_line == 14
    assert "Ignore all prior instructions" in first.chunks[0].content
    assert any("# This heading is inert" in item.content for item in first.chunks)


def test_plain_text_paragraphs_split_deterministically() -> None:
    source = (FIXTURE / "docs/notes.rst").read_text()
    result = documentation_chunker(max_chunk_bytes=80).chunk(
        file_path="docs/notes.rst",
        source_text=source,
        repository_id=REPOSITORY_ID,
        index_version=2,
        commit_sha=COMMIT,
    )

    assert [item.chunk_type for item in result.chunks] == [
        ChunkType.DOCUMENTATION,
        ChunkType.DOCUMENTATION,
    ]
    assert [(item.start_line, item.end_line) for item in result.chunks] == [
        (1, 3),
        (5, 5),
    ]


def test_documentation_limits_and_unsupported_files_are_classified() -> None:
    too_large = documentation_chunker(max_chunk_bytes=32, max_section_bytes=40).chunk(
        file_path="large.md",
        source_text="# Large\n\n" + ("content " * 20),
        repository_id=REPOSITORY_ID,
        index_version=1,
        commit_sha=COMMIT,
    )
    unsupported = documentation_chunker().chunk(
        file_path="settings.toml",
        source_text="key = 'value'",
        repository_id=REPOSITORY_ID,
        index_version=1,
        commit_sha=COMMIT,
    )

    assert too_large.chunks == ()
    assert too_large.warnings == ("documentation_section_too_large",)
    assert unsupported.warnings == ("unsupported_file",)
