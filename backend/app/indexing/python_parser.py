"""Tree-sitter Python parsing without importing or executing repository code."""

import hashlib
from collections.abc import Iterable
from pathlib import PurePosixPath

import tree_sitter_python
from tree_sitter import Language, Node, Parser

from app.db.models.enums import SymbolType
from app.indexing.models import (
    ImportAlias,
    ParameterKind,
    ParsedCallSite,
    ParsedFile,
    ParsedImport,
    ParsedParameter,
    ParsedSymbol,
    ParseStatus,
    SourceSegment,
)

PYTHON_LANGUAGE = Language(tree_sitter_python.language())
DEFINITION_TYPES = frozenset({"function_definition", "class_definition"})


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PythonStaticParser:
    """Extract structure from UTF-8 Python text using Tree-sitter only."""

    def __init__(
        self,
        *,
        max_symbols: int,
        max_warnings: int,
        max_call_sites: int = 10_000,
        max_call_expression_bytes: int = 2048,
    ) -> None:
        self._max_symbols = max_symbols
        self._max_warnings = max_warnings
        self._max_call_sites = max_call_sites
        self._max_call_expression_bytes = max_call_expression_bytes
        self._parser = Parser(PYTHON_LANGUAGE)

    def parse(self, *, file_path: str, source_text: str, commit_sha: str) -> ParsedFile:
        normalized = source_text.replace("\r\n", "\n").replace("\r", "\n")
        source = normalized.encode("utf-8")
        tree = self._parser.parse(source)
        root = tree.root_node
        imports = tuple(self._extract_imports(root, source))
        module_name = self._module_name(file_path)
        symbols: list[ParsedSymbol] = []
        self._walk_definitions(
            node=root,
            source=source,
            file_path=file_path,
            module_name=module_name,
            commit_sha=commit_sha,
            parents=(),
            output=symbols,
        )
        warnings: list[str] = []
        if root.has_error:
            warnings.append("malformed_python")
        if len(symbols) > self._max_symbols:
            return ParsedFile(
                file_path=file_path,
                language="python",
                module_name=module_name,
                source_text="",
                imports=(),
                module_docstring=None,
                module_segments=(),
                symbols=(),
                call_sites=(),
                commit_sha=commit_sha,
                parse_status=ParseStatus.SKIPPED,
                warnings=("symbol_count_exceeded",),
            )
        module_segments = tuple(
            self._segments(
                (
                    child
                    for child in root.named_children
                    if child.type not in DEFINITION_TYPES
                    and child.type
                    not in {"decorated_definition", "import_statement", "import_from_statement"}
                ),
                source,
            )
        )
        call_sites, call_warnings = self._extract_call_sites(
            root=root,
            source=source,
            file_path=file_path,
            module_name=module_name,
            symbols=symbols,
        )
        warnings.extend(call_warnings)
        status = ParseStatus.COMPLETE
        if root.has_error:
            status = (
                ParseStatus.PARTIAL
                if symbols or imports or module_segments
                else ParseStatus.MALFORMED
            )
        return ParsedFile(
            file_path=file_path,
            language="python",
            module_name=module_name,
            source_text=normalized,
            imports=imports,
            module_docstring=self._docstring(root, source),
            module_segments=module_segments,
            symbols=tuple(
                sorted(
                    symbols,
                    key=lambda item: (item.start_line, item.end_line, item.qualified_name),
                )
            ),
            call_sites=call_sites,
            commit_sha=commit_sha,
            parse_status=status,
            warnings=tuple(warnings[: self._max_warnings]),
        )

    def _extract_call_sites(
        self,
        *,
        root: Node,
        source: bytes,
        file_path: str,
        module_name: str,
        symbols: list[ParsedSymbol],
    ) -> tuple[tuple[ParsedCallSite, ...], list[str]]:
        callable_symbols = tuple(
            symbol
            for symbol in symbols
            if symbol.symbol_type
            in {SymbolType.FUNCTION, SymbolType.ASYNC_FUNCTION, SymbolType.METHOD}
        )
        pending = [root]
        result: list[ParsedCallSite] = []
        warnings: list[str] = []
        while pending:
            node = pending.pop()
            if node.type == "call":
                function = node.child_by_field_name("function")
                line = node.start_point.row + 1
                containing = [
                    symbol
                    for symbol in callable_symbols
                    if symbol.header_end_line <= line <= symbol.end_line
                ]
                caller = min(
                    containing,
                    key=lambda item: (item.end_line - item.start_line, -item.start_line),
                    default=None,
                )
                if function is not None and caller is not None:
                    expression = self._text(function, source)
                    if len(expression.encode("utf-8")) <= self._max_call_expression_bytes:
                        result.append(
                            ParsedCallSite(
                                file_path=file_path,
                                module_name=module_name,
                                caller_qualified_name=caller.qualified_name,
                                expression=expression,
                                start_line=line,
                                end_line=node.end_point.row + 1,
                            )
                        )
                    else:
                        warnings.append("call_expression_too_large")
            pending.extend(reversed(node.named_children))
            if len(result) > self._max_call_sites:
                return (), ["call_site_count_exceeded"]
        return (
            tuple(
                sorted(
                    result,
                    key=lambda item: (
                        item.start_line,
                        item.end_line,
                        item.caller_qualified_name,
                        item.expression,
                    ),
                )
            ),
            warnings,
        )

    def _walk_definitions(
        self,
        *,
        node: Node,
        source: bytes,
        file_path: str,
        module_name: str,
        commit_sha: str,
        parents: tuple[ParsedSymbol, ...],
        output: list[ParsedSymbol],
    ) -> None:
        if len(output) > self._max_symbols:
            return
        for child in node.named_children:
            if len(output) > self._max_symbols:
                return
            wrapper = child
            decorators: tuple[str, ...] = ()
            definition = child
            if child.type == "decorated_definition":
                candidate = child.child_by_field_name("definition")
                if candidate is None:
                    continue
                definition = candidate
                decorators = tuple(
                    self._text(item, source)
                    for item in child.named_children
                    if item.type == "decorator"
                )
            if definition.type in DEFINITION_TYPES:
                symbol = self._symbol(
                    wrapper=wrapper,
                    definition=definition,
                    decorators=decorators,
                    source=source,
                    file_path=file_path,
                    module_name=module_name,
                    commit_sha=commit_sha,
                    parents=parents,
                )
                if symbol is not None:
                    output.append(symbol)
                    body = definition.child_by_field_name("body")
                    if body is not None:
                        self._walk_definitions(
                            node=body,
                            source=source,
                            file_path=file_path,
                            module_name=module_name,
                            commit_sha=commit_sha,
                            parents=(*parents, symbol),
                            output=output,
                        )
                continue
            self._walk_definitions(
                node=child,
                source=source,
                file_path=file_path,
                module_name=module_name,
                commit_sha=commit_sha,
                parents=parents,
                output=output,
            )

    def _symbol(
        self,
        *,
        wrapper: Node,
        definition: Node,
        decorators: tuple[str, ...],
        source: bytes,
        file_path: str,
        module_name: str,
        commit_sha: str,
        parents: tuple[ParsedSymbol, ...],
    ) -> ParsedSymbol | None:
        name_node = definition.child_by_field_name("name")
        body = definition.child_by_field_name("body")
        if name_node is None or body is None:
            return None
        name = self._text(name_node, source)
        qualified_name = ".".join((module_name, *(item.name for item in parents), name))
        parent = parents[-1] if parents else None
        is_function = definition.type == "function_definition"
        is_async = is_function and any(child.type == "async" for child in definition.children)
        if definition.type == "class_definition":
            symbol_type = SymbolType.CLASS
        elif parent is not None and parent.symbol_type is SymbolType.CLASS:
            symbol_type = SymbolType.METHOD
        elif is_async:
            symbol_type = SymbolType.ASYNC_FUNCTION
        else:
            symbol_type = SymbolType.FUNCTION
        parameters_node = definition.child_by_field_name("parameters")
        return_node = definition.child_by_field_name("return_type")
        source_text = self._text(wrapper, source)
        signature = self._text_range(definition.start_byte, body.start_byte, source).rstrip()
        header_text = self._text_range(wrapper.start_byte, body.start_byte, source).rstrip()
        return ParsedSymbol(
            file_path=file_path,
            language="python",
            symbol_type=symbol_type,
            name=name,
            is_async=is_async,
            qualified_name=qualified_name,
            parent_qualified_name=None if parent is None else parent.qualified_name,
            decorators=decorators,
            signature=signature,
            header_text=header_text,
            header_start_line=wrapper.start_point.row + 1,
            header_end_line=definition.start_point.row + 1 + signature.count("\n"),
            parameters=() if parameters_node is None else self._parameters(parameters_node, source),
            return_annotation=None if return_node is None else self._text(return_node, source),
            docstring=self._docstring(body, source),
            source_text=source_text,
            body_segments=tuple(self._segments(body.named_children, source)),
            start_line=wrapper.start_point.row + 1,
            end_line=wrapper.end_point.row + 1,
            commit_sha=commit_sha,
            content_hash=content_hash(source_text),
        )

    def _parameters(self, parameters: Node, source: bytes) -> tuple[ParsedParameter, ...]:
        result: list[ParsedParameter] = []
        keyword_only = False
        for node in parameters.children:
            if node.type == "positional_separator":
                result = [
                    ParsedParameter(
                        name=item.name,
                        kind=ParameterKind.POSITIONAL_ONLY,
                        annotation=item.annotation,
                        default=item.default,
                    )
                    if item.kind is ParameterKind.POSITIONAL_OR_KEYWORD
                    else item
                    for item in result
                ]
                continue
            if node.type == "keyword_separator":
                keyword_only = True
                continue
            if not node.is_named:
                continue
            text = self._text(node, source)
            if text.startswith("**"):
                kind = ParameterKind.VAR_KEYWORD
            elif text.startswith("*"):
                kind = ParameterKind.VAR_POSITIONAL
                keyword_only = True
            elif keyword_only:
                kind = ParameterKind.KEYWORD_ONLY
            else:
                kind = ParameterKind.POSITIONAL_OR_KEYWORD
            name_node = node.child_by_field_name("name") or self._first_identifier(node)
            if name_node is None:
                continue
            type_node = node.child_by_field_name("type")
            value_node = node.child_by_field_name("value")
            result.append(
                ParsedParameter(
                    name=self._text(name_node, source),
                    kind=kind,
                    annotation=None if type_node is None else self._text(type_node, source),
                    default=None if value_node is None else self._text(value_node, source),
                )
            )
        return tuple(result)

    def _extract_imports(self, root: Node, source: bytes) -> Iterable[ParsedImport]:
        pending = [root]
        imports: list[ParsedImport] = []
        while pending:
            node = pending.pop()
            if node.type in {"import_statement", "import_from_statement"}:
                imports.append(self._import(node, source))
                continue
            pending.extend(reversed(node.named_children))
        return sorted(imports, key=lambda item: (item.start_line, item.end_line, item.source_text))

    def _import(self, node: Node, source: bytes) -> ParsedImport:
        module_node = node.child_by_field_name("module_name")
        module_text = None if module_node is None else self._text(module_node, source)
        relative_level = 0
        module = module_text
        if module_text is not None:
            relative_level = len(module_text) - len(module_text.lstrip("."))
            module = module_text[relative_level:] or None
        names: list[ImportAlias] = []
        for index, child in enumerate(node.children):
            if node.field_name_for_child(index) != "name" and node.type == "import_from_statement":
                continue
            if not child.is_named:
                continue
            if child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if name_node is not None:
                    names.append(
                        ImportAlias(
                            name=self._text(name_node, source),
                            alias=None if alias_node is None else self._text(alias_node, source),
                        )
                    )
            elif child.type in {"dotted_name", "identifier", "wildcard_import"}:
                if node.type == "import_from_statement" and child == module_node:
                    continue
                names.append(ImportAlias(name=self._text(child, source), alias=None))
        return ParsedImport(
            module=module,
            relative_level=relative_level,
            names=tuple(names),
            source_text=self._text(node, source),
            start_line=node.start_point.row + 1,
            end_line=node.end_point.row + 1,
        )

    @classmethod
    def _docstring(cls, container: Node, source: bytes) -> str | None:
        if not container.named_children:
            return None
        first = container.named_children[0]
        if first.type != "expression_statement" or not first.named_children:
            return None
        value = first.named_children[0]
        if value.type not in {"string", "concatenated_string"}:
            return None
        return cls._text(value, source)

    @classmethod
    def _segments(cls, nodes: Iterable[Node], source: bytes) -> Iterable[SourceSegment]:
        for node in nodes:
            yield SourceSegment(
                text=cls._text(node, source),
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
                node_type=node.type,
            )

    @classmethod
    def _first_identifier(cls, node: Node) -> Node | None:
        if node.type == "identifier":
            return node
        for child in node.named_children:
            result = cls._first_identifier(child)
            if result is not None:
                return result
        return None

    @staticmethod
    def _module_name(file_path: str) -> str:
        path = PurePosixPath(file_path)
        parts = list(path.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts) if parts else path.stem

    @staticmethod
    def _text(node: Node, source: bytes) -> str:
        return source[node.start_byte : node.end_byte].decode("utf-8")

    @staticmethod
    def _text_range(start: int, end: int, source: bytes) -> str:
        return source[start:end].decode("utf-8")
