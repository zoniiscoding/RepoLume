"""Static call extraction and conservative resolution behavior."""

import uuid

import pytest

from app.db.models.enums import Confidence, ResolutionType
from app.indexing.call_graph import CallGraph, PythonCallGraphBuilder
from app.indexing.failures import IndexingError
from app.indexing.models import ParsedFile, SymbolRecord
from app.indexing.python_parser import PythonStaticParser

REPOSITORY_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
COMMIT = "8" * 40


def parse(file_path: str, source: str, *, max_calls: int = 100) -> ParsedFile:
    return PythonStaticParser(
        max_symbols=100,
        max_warnings=20,
        max_call_sites=max_calls,
        max_call_expression_bytes=256,
    ).parse(file_path=file_path, source_text=source, commit_sha=COMMIT)


def records(files: tuple[ParsedFile, ...]) -> tuple[SymbolRecord, ...]:
    return tuple(
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
        for parsed in files
        for symbol in parsed.symbols
    )


def graph(
    files: tuple[ParsedFile, ...],
    *,
    version: int = 3,
    repository_id: uuid.UUID = REPOSITORY_ID,
) -> CallGraph:
    return PythonCallGraphBuilder(max_total_call_sites=1000).build(
        repository_id=repository_id,
        index_version=version,
        commit_sha=COMMIT,
        parsed_files=files,
        symbols=records(files),
    )


def test_resolves_same_file_nested_direct_import_module_alias_and_self_calls() -> None:
    files = (
        parse(
            "pkg/target.py",
            "def imported():\n    return 1\n\nclass Worker:\n"
            "    def run(self):\n        return imported()\n",
        ),
        parse(
            "pkg/caller.py",
            "from .target import imported as direct\nimport pkg.target as target_module\n\n"
            "def helper():\n    return 1\n\ndef outer():\n"
            "    def nested():\n        return helper()\n    direct()\n"
            "    target_module.imported()\n    return nested()\n\n"
            "class Local:\n    def first(self):\n        return self.second()\n\n"
            "    def second(self):\n        return helper()\n",
        ),
    )

    result = graph(files)
    resolved = {
        (edge.call_expression, edge.resolution_type, edge.confidence) for edge in result.edges
    }

    assert ("direct", ResolutionType.EXACT_DIRECT_IMPORT, Confidence.HIGH) in resolved
    assert (
        "target_module.imported",
        ResolutionType.QUALIFIED_MODULE,
        Confidence.HIGH,
    ) in resolved
    assert ("nested", ResolutionType.EXACT_SAME_FILE, Confidence.HIGH) in resolved
    assert ("self.second", ResolutionType.EXACT_SAME_FILE, Confidence.HIGH) in resolved
    assert result.exact_edge_count >= 6
    assert result.unresolved_call_count == 0


def test_async_multiline_constructor_probable_and_dynamic_calls_are_classified() -> None:
    files = (
        parse(
            "service.py",
            "class Client:\n    def send(self):\n        return 1\n\n"
            "async def target():\n    return 1\n",
        ),
        parse(
            "caller.py",
            "from service import Client, target\n\nasync def invoke(obj, name):\n"
            "    await target(\n        )\n    Client()\n    obj.send()\n"
            "    getattr(obj, name)()\n",
        ),
    )

    result = graph(files)
    by_expression = {edge.call_expression: edge for edge in result.edges}

    assert by_expression["target"].resolution_type is ResolutionType.EXACT_DIRECT_IMPORT
    assert by_expression["Client"].resolution_type is ResolutionType.EXACT_DIRECT_IMPORT
    assert by_expression["obj.send"].resolution_type is ResolutionType.PROBABLE_METHOD
    assert by_expression["obj.send"].confidence is Confidence.MEDIUM
    assert by_expression["getattr(obj, name)"].resolution_type is ResolutionType.UNRESOLVED
    assert by_expression["getattr(obj, name)"].callee_symbol_id is None
    assert result.unresolved_call_count >= 1


def test_ambiguous_method_and_wildcard_import_never_become_exact_edges() -> None:
    files = (
        parse("one.py", "class One:\n    def run(self):\n        return 1\n"),
        parse("two.py", "class Two:\n    def run(self):\n        return 2\n"),
        parse(
            "caller.py",
            "from one import *\n\ndef invoke(value):\n    value.run()\n    unknown()\n",
        ),
    )

    result = graph(files)
    by_expression = {edge.call_expression: edge for edge in result.edges}

    assert by_expression["value.run"].resolution_type is ResolutionType.AMBIGUOUS
    assert by_expression["value.run"].confidence is Confidence.LOW
    assert by_expression["unknown"].resolution_type is ResolutionType.UNRESOLVED
    assert result.ambiguous_edge_count == 1
    assert result.unresolved_call_count == 1
    assert result.warning_count == 1


def test_graph_identity_is_deterministic_and_version_scoped() -> None:
    files = (
        parse(
            "module.py",
            "def target():\n    pass\n\ndef café():\n    target()\n\ndef caller():\n    café()\n",
        ),
    )

    first = graph(files, version=1)
    repeated = graph(files, version=1)
    next_version = graph(files, version=2)
    other_repository = graph(
        files,
        version=1,
        repository_id=uuid.UUID("55555555-5555-5555-5555-555555555555"),
    )

    assert first == repeated
    assert first.fingerprint == repeated.fingerprint
    assert any(edge.call_expression == "café" and edge.callee_symbol_id for edge in first.edges)
    assert first.edges[0].id != next_version.edges[0].id
    assert first.edges[0].caller_symbol_id != next_version.edges[0].caller_symbol_id
    assert first.edges[0].id != other_repository.edges[0].id
    assert first.edges[0].caller_symbol_id != other_repository.edges[0].caller_symbol_id


def test_per_file_and_repository_call_limits_fail_safely() -> None:
    limited = parse("limited.py", "def caller():\n    one()\n    two()\n", max_calls=1)
    assert limited.call_sites == ()
    assert limited.warnings == ("call_site_count_exceeded",)

    regular = parse("regular.py", "def caller():\n    one()\n    two()\n")
    with pytest.raises(IndexingError, match="call_site_count_exceeded"):
        PythonCallGraphBuilder(max_total_call_sites=1).build(
            repository_id=REPOSITORY_ID,
            index_version=1,
            commit_sha=COMMIT,
            parsed_files=(regular,),
            symbols=records((regular,)),
        )
