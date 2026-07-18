"""Deterministic, bounded Python static call-graph construction."""

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.db.models.enums import Confidence, ResolutionType, SymbolType
from app.indexing.failures import IndexingError
from app.indexing.models import (
    CallEdgeRecord,
    ParsedCallSite,
    ParsedFile,
    ParsedImport,
    SymbolRecord,
)

_SYMBOL_NAMESPACE = uuid.UUID("1b9f66f0-2676-4a3e-b0da-73c9432c74cc")
_EDGE_NAMESPACE = uuid.UUID("14f608ec-d9dc-4dd8-a182-7a9cd7b3f5c7")
_MIN_QUALIFIED_PARTS = 2


@dataclass(frozen=True, slots=True)
class CallGraph:
    edges: tuple[CallEdgeRecord, ...]
    call_site_count: int
    exact_edge_count: int
    ambiguous_edge_count: int
    unresolved_call_count: int
    warning_count: int
    fingerprint: str


@dataclass(frozen=True, slots=True)
class _Resolution:
    callee: SymbolRecord | None
    kind: ResolutionType
    confidence: Confidence
    ambiguous: bool = False


def deterministic_symbol_id(
    repository_id: uuid.UUID,
    index_version: int,
    symbol: SymbolRecord,
) -> uuid.UUID:
    identity = "\x1f".join(
        (
            str(repository_id),
            str(index_version),
            symbol.file_path,
            symbol.qualified_name,
            str(symbol.start_line),
            symbol.content_hash,
        )
    )
    return uuid.uuid5(_SYMBOL_NAMESPACE, identity)


class PythonCallGraphBuilder:
    """Resolve only statically defensible Python calls; ambiguity fails closed."""

    def __init__(self, *, max_total_call_sites: int) -> None:
        self._max_total_call_sites = max_total_call_sites

    def build(
        self,
        *,
        repository_id: uuid.UUID,
        index_version: int,
        commit_sha: str,
        parsed_files: tuple[ParsedFile, ...],
        symbols: tuple[SymbolRecord, ...],
    ) -> CallGraph:
        sites = tuple(site for parsed in parsed_files for site in parsed.call_sites)
        if len(sites) > self._max_total_call_sites:
            raise IndexingError(
                code="call_site_count_exceeded",
                message="Repository exceeds the configured static call-site limit",
                retryable=False,
            )
        by_qualified = self._group(symbols, lambda item: item.qualified_name)
        by_name = self._group(symbols, lambda item: item.symbol_name)
        files = {parsed.file_path: parsed for parsed in parsed_files}
        edges: list[CallEdgeRecord] = []
        ambiguous = 0
        unresolved = 0
        exact = 0
        skipped = 0
        for site in sites:
            caller = self._one(by_qualified.get(site.caller_qualified_name, ()))
            parsed = files.get(site.file_path)
            if caller is None or parsed is None:
                skipped += 1
                continue
            resolution = self._resolve(site, caller, parsed, by_qualified, by_name)
            ambiguous += int(resolution.kind is ResolutionType.AMBIGUOUS)
            unresolved += int(resolution.kind is ResolutionType.UNRESOLVED)
            exact += int(
                resolution.callee is not None
                and resolution.kind is not ResolutionType.PROBABLE_METHOD
            )
            callee_id = (
                None
                if resolution.callee is None
                else deterministic_symbol_id(repository_id, index_version, resolution.callee)
            )
            site_fingerprint = hashlib.sha256(
                "\x1f".join(
                    (
                        site.file_path,
                        site.caller_qualified_name,
                        site.expression,
                        str(site.start_line),
                        str(site.end_line),
                    )
                ).encode()
            ).hexdigest()
            caller_id = deterministic_symbol_id(repository_id, index_version, caller)
            edge_id = uuid.uuid5(
                _EDGE_NAMESPACE,
                "\x1f".join(
                    (str(repository_id), str(index_version), str(caller_id), site_fingerprint)
                ),
            )
            edges.append(
                CallEdgeRecord(
                    id=edge_id,
                    caller_symbol_id=caller_id,
                    callee_symbol_id=callee_id,
                    unresolved_callee_name=None if callee_id is not None else site.expression,
                    file_path=site.file_path,
                    call_line=site.start_line,
                    call_end_line=site.end_line,
                    call_expression=site.expression,
                    call_site_fingerprint=site_fingerprint,
                    resolution_type=resolution.kind,
                    confidence=resolution.confidence,
                    commit_sha=commit_sha,
                )
            )
        ordered = tuple(
            sorted(
                edges,
                key=lambda item: (
                    item.file_path,
                    item.call_line,
                    item.call_end_line,
                    str(item.caller_symbol_id),
                    item.call_expression,
                ),
            )
        )
        fingerprint = hashlib.sha256(
            "\n".join(
                "\x1f".join(
                    (
                        str(item.id),
                        str(item.caller_symbol_id),
                        str(item.callee_symbol_id or ""),
                        item.call_site_fingerprint,
                        item.resolution_type.value,
                        item.confidence.value,
                    )
                )
                for item in ordered
            ).encode()
        ).hexdigest()
        return CallGraph(
            edges=ordered,
            call_site_count=len(ordered),
            exact_edge_count=exact,
            ambiguous_edge_count=ambiguous,
            unresolved_call_count=unresolved,
            warning_count=ambiguous + skipped,
            fingerprint=fingerprint,
        )

    def _resolve(
        self,
        site: ParsedCallSite,
        caller: SymbolRecord,
        parsed: ParsedFile,
        by_qualified: dict[str, tuple[SymbolRecord, ...]],
        by_name: dict[str, tuple[SymbolRecord, ...]],
    ) -> _Resolution:
        expression = site.expression
        if expression.isidentifier():
            return self._resolve_identifier(expression, caller, parsed, by_qualified)
        parts = expression.split(".")
        if len(parts) < _MIN_QUALIFIED_PARTS or not all(part.isidentifier() for part in parts):
            return _Resolution(None, ResolutionType.UNRESOLVED, Confidence.LOW)
        return self._resolve_attribute(parts, caller, parsed, by_qualified, by_name)

    def _resolve_identifier(
        self,
        expression: str,
        caller: SymbolRecord,
        parsed: ParsedFile,
        by_qualified: dict[str, tuple[SymbolRecord, ...]],
    ) -> _Resolution:
        local_candidates = self._local_candidates(caller, parsed.module_name, expression)
        resolution = self._first_unique(local_candidates, by_qualified)
        if resolution is not None:
            return self._exact_or_ambiguous(resolution, ResolutionType.EXACT_SAME_FILE)
        imported = self._direct_import_candidates(parsed, expression)
        resolution = self._first_unique(imported, by_qualified)
        if resolution is not None:
            return self._exact_or_ambiguous(resolution, ResolutionType.EXACT_DIRECT_IMPORT)
        return _Resolution(None, ResolutionType.UNRESOLVED, Confidence.LOW)

    def _resolve_attribute(
        self,
        parts: list[str],
        caller: SymbolRecord,
        parsed: ParsedFile,
        by_qualified: dict[str, tuple[SymbolRecord, ...]],
        by_name: dict[str, tuple[SymbolRecord, ...]],
    ) -> _Resolution:
        if parts[0] in {"self", "cls"}:
            class_name = caller.qualified_name.rpartition(".")[0]
            matches = by_qualified.get(f"{class_name}.{parts[-1]}", ())
            return self._exact_or_ambiguous(matches, ResolutionType.EXACT_SAME_FILE)
        imported = self._qualified_import_candidates(parsed, parts)
        resolution = self._first_unique(imported, by_qualified)
        if resolution is not None:
            return self._exact_or_ambiguous(resolution, ResolutionType.QUALIFIED_MODULE)
        same_module = by_qualified.get(f"{parsed.module_name}.{'.'.join(parts)}", ())
        if same_module:
            return self._exact_or_ambiguous(same_module, ResolutionType.EXACT_SAME_FILE)
        methods = tuple(
            item for item in by_name.get(parts[-1], ()) if item.symbol_type is SymbolType.METHOD
        )
        if len(methods) == 1:
            return _Resolution(methods[0], ResolutionType.PROBABLE_METHOD, Confidence.MEDIUM)
        return _Resolution(
            None,
            ResolutionType.AMBIGUOUS if len(methods) > 1 else ResolutionType.UNRESOLVED,
            Confidence.LOW,
            ambiguous=len(methods) > 1,
        )

    @staticmethod
    def _local_candidates(
        caller: SymbolRecord,
        module_name: str,
        target: str,
    ) -> tuple[str, ...]:
        parent = caller.qualified_name
        candidates: list[str] = []
        while parent and (parent == module_name or parent.startswith(f"{module_name}.")):
            candidates.append(f"{parent}.{target}")
            if parent == module_name:
                break
            parent = parent.rpartition(".")[0]
        return tuple(candidates)

    def _direct_import_candidates(self, parsed: ParsedFile, target: str) -> tuple[str, ...]:
        result: list[str] = []
        for item in parsed.imports:
            if item.module is None and item.relative_level == 0:
                continue
            module = self._absolute_module(parsed, item)
            for imported in item.names:
                if imported.name == "*" or (imported.alias or imported.name) != target:
                    continue
                result.append(".".join(filter(None, (module, imported.name))))
        return tuple(result)

    def _qualified_import_candidates(
        self,
        parsed: ParsedFile,
        parts: list[str],
    ) -> tuple[str, ...]:
        result: list[str] = []
        for item in parsed.imports:
            if item.module is None and item.relative_level == 0:
                for imported in item.names:
                    binding = imported.alias or imported.name.split(".")[0]
                    if binding == parts[0]:
                        base = imported.name if imported.alias else parts[0]
                        result.append(".".join((base, *parts[1:])))
                continue
            module = self._absolute_module(parsed, item)
            for imported in item.names:
                if imported.name == "*" or (imported.alias or imported.name) != parts[0]:
                    continue
                result.append(".".join(filter(None, (module, imported.name, *parts[1:]))))
        return tuple(result)

    @staticmethod
    def _absolute_module(parsed: ParsedFile, imported: ParsedImport) -> str:
        if imported.relative_level == 0:
            return imported.module or ""
        path = PurePosixPath(parsed.file_path)
        package = (
            parsed.module_name if path.stem == "__init__" else parsed.module_name.rpartition(".")[0]
        )
        parts = package.split(".") if package else []
        ascend = max(0, imported.relative_level - 1)
        if ascend:
            parts = parts[:-ascend] if ascend <= len(parts) else []
        return ".".join((*parts, *((imported.module or "").split(".")))).strip(".")

    @staticmethod
    def _group(
        symbols: tuple[SymbolRecord, ...],
        key: Callable[[SymbolRecord], str],
    ) -> dict[str, tuple[SymbolRecord, ...]]:
        output: dict[str, list[SymbolRecord]] = {}
        for symbol in symbols:
            value = key(symbol)
            output.setdefault(value, []).append(symbol)
        return {name: tuple(items) for name, items in output.items()}

    @staticmethod
    def _one(items: tuple[SymbolRecord, ...]) -> SymbolRecord | None:
        return items[0] if len(items) == 1 else None

    @staticmethod
    def _first_unique(
        candidates: tuple[str, ...],
        by_qualified: dict[str, tuple[SymbolRecord, ...]],
    ) -> tuple[SymbolRecord, ...] | None:
        for candidate in candidates:
            matches = by_qualified.get(candidate, ())
            if matches:
                return matches
        return None

    @staticmethod
    def _exact_or_ambiguous(
        matches: tuple[SymbolRecord, ...],
        kind: ResolutionType,
    ) -> _Resolution:
        if len(matches) == 1:
            return _Resolution(matches[0], kind, Confidence.HIGH)
        return _Resolution(
            None,
            ResolutionType.AMBIGUOUS if matches else ResolutionType.UNRESOLVED,
            Confidence.LOW,
            ambiguous=bool(matches),
        )
