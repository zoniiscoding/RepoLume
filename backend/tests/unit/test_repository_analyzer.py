"""Repository-wide bounds, determinism, isolation, and safe classifications."""

import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.indexing.analyzer import ParserLimits, ProcessIsolatedAnalyzer, RepositoryAnalyzer
from app.indexing.discovery import DiscoveredFile, DiscoveryResult, FileDiscovery
from app.indexing.failures import IndexingError
from tests.conftest import make_settings

FIXTURE = Path(__file__).parents[1] / "fixtures" / "milestone4_repository"
REPOSITORY_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
COMMIT = "c" * 40


def limits(**overrides: int | float) -> ParserLimits:
    values: dict[str, int | float] = {
        "max_input_bytes": 2 * 1024 * 1024,
        "max_symbols_per_file": 100,
        "max_symbol_bytes": 512 * 1024,
        "max_chunk_bytes": 32 * 1024,
        "max_chunks_per_file": 100,
        "max_total_chunks": 1000,
        "max_total_chunk_bytes": 64 * 1024 * 1024,
        "max_document_section_bytes": 256 * 1024,
        "max_warnings_per_file": 10,
        "max_call_sites_per_file": 1000,
        "max_total_call_sites": 10_000,
        "max_call_expression_bytes": 2048,
        "timeout_seconds": 10.0,
        "process_memory_bytes": 2 * 1024 * 1024 * 1024,
        "process_cpu_seconds": 5,
    }
    values.update(overrides)
    return ParserLimits(
        max_input_bytes=int(values["max_input_bytes"]),
        max_symbols_per_file=int(values["max_symbols_per_file"]),
        max_symbol_bytes=int(values["max_symbol_bytes"]),
        max_chunk_bytes=int(values["max_chunk_bytes"]),
        max_chunks_per_file=int(values["max_chunks_per_file"]),
        max_total_chunks=int(values["max_total_chunks"]),
        max_total_chunk_bytes=int(values["max_total_chunk_bytes"]),
        max_document_section_bytes=int(values["max_document_section_bytes"]),
        max_warnings_per_file=int(values["max_warnings_per_file"]),
        max_call_sites_per_file=int(values["max_call_sites_per_file"]),
        max_total_call_sites=int(values["max_total_call_sites"]),
        max_call_expression_bytes=int(values["max_call_expression_bytes"]),
        timeout_seconds=float(values["timeout_seconds"]),
        process_memory_bytes=int(values["process_memory_bytes"]),
        process_cpu_seconds=int(values["process_cpu_seconds"]),
    )


def checkout(tmp_path: Path) -> tuple[Path, DiscoveryResult]:
    root = tmp_path / "checkout"
    shutil.copytree(FIXTURE, root)
    discovery = FileDiscovery(make_settings()).discover(root)
    return root, discovery


def test_repeated_repository_processing_is_identical(tmp_path: Path) -> None:
    root, discovery = checkout(tmp_path)
    analyzer = RepositoryAnalyzer(limits())

    first = analyzer.analyze(
        checkout=root,
        discovery=discovery,
        repository_id=REPOSITORY_ID,
        index_version=7,
        commit_sha=COMMIT,
    )
    second = analyzer.analyze(
        checkout=root,
        discovery=discovery,
        repository_id=REPOSITORY_ID,
        index_version=7,
        commit_sha=COMMIT,
    )

    assert first == second
    assert first.parsed_file_count == 3
    assert first.partial_file_count == 1
    assert first.skipped_file_count == 0
    assert first.symbol_count == 8
    assert first.chunk_count > 0
    assert [item.ordinal for item in first.chunk_fingerprints] == list(range(first.chunk_count))
    assert first.warning_counts == {"malformed_python": 1}


def test_repository_results_do_not_mix_repository_identity(tmp_path: Path) -> None:
    root, discovery = checkout(tmp_path)
    analyzer = RepositoryAnalyzer(limits())
    other_repository = uuid.UUID("33333333-3333-3333-3333-333333333333")

    first = analyzer.analyze(
        checkout=root,
        discovery=discovery,
        repository_id=REPOSITORY_ID,
        index_version=1,
        commit_sha=COMMIT,
    )
    second = analyzer.analyze(
        checkout=root,
        discovery=discovery,
        repository_id=other_repository,
        index_version=1,
        commit_sha=COMMIT,
    )

    assert first.repository_id == REPOSITORY_ID
    assert second.repository_id == other_repository
    assert first.symbols == second.symbols
    assert first.chunk_fingerprints == second.chunk_fingerprints


def test_input_and_repository_chunk_limits_are_enforced(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    source = "def one():\n    return 1\n\ndef two():\n    return 2\n"
    path = root / "many.py"
    path.write_text(source)
    discovery = DiscoveryResult(
        files=(DiscoveredFile("many.py", path.stat().st_size),),
        inspected_file_count=1,
        total_bytes=path.stat().st_size,
        skipped={},
    )
    input_limited = RepositoryAnalyzer(limits(max_input_bytes=10)).analyze(
        checkout=root,
        discovery=discovery,
        repository_id=REPOSITORY_ID,
        index_version=1,
        commit_sha=COMMIT,
    )

    assert input_limited.skipped_file_count == 1
    assert input_limited.warning_counts == {"parser_input_too_large": 1}
    with pytest.raises(IndexingError, match="chunk_count_exceeded"):
        RepositoryAnalyzer(limits(max_total_chunks=1)).analyze(
            checkout=root,
            discovery=discovery,
            repository_id=REPOSITORY_ID,
            index_version=1,
            commit_sha=COMMIT,
        )


def test_unsafe_discovery_path_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n")
    discovery = DiscoveryResult(
        files=(DiscoveredFile("../outside.py", outside.stat().st_size),),
        inspected_file_count=1,
        total_bytes=outside.stat().st_size,
        skipped={},
    )

    with pytest.raises(IndexingError, match="unsafe_repository_path"):
        RepositoryAnalyzer(limits()).analyze(
            checkout=root,
            discovery=discovery,
            repository_id=REPOSITORY_ID,
            index_version=1,
            commit_sha=COMMIT,
        )


def test_parser_exception_is_reduced_to_safe_file_classification(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    path = root / "private.py"
    path.write_text("private source sentinel")
    discovery = DiscoveryResult(
        files=(DiscoveredFile("private.py", path.stat().st_size),),
        inspected_file_count=1,
        total_bytes=path.stat().st_size,
        skipped={},
    )
    analyzer = RepositoryAnalyzer(limits())
    analyzer._python_parser.parse = MagicMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("private source sentinel")
    )

    result = analyzer.analyze(
        checkout=root,
        discovery=discovery,
        repository_id=REPOSITORY_ID,
        index_version=1,
        commit_sha=COMMIT,
    )

    assert result.skipped_file_count == 1
    assert result.warning_counts == {"internal_parser_failure": 1}
    assert "private source sentinel" not in repr(result)


@pytest.mark.asyncio
async def test_isolated_processor_timeout_is_safe_and_killable(tmp_path: Path) -> None:
    root, discovery = checkout(tmp_path)
    analyzer = ProcessIsolatedAnalyzer(replace(limits(), timeout_seconds=0.001))

    async def on_chunking() -> None:
        return None

    with pytest.raises(IndexingError, match="parser_timeout"):
        await analyzer.analyze(
            checkout=root,
            discovery=discovery,
            repository_id=REPOSITORY_ID,
            index_version=1,
            commit_sha=COMMIT,
            on_chunking=on_chunking,
        )
