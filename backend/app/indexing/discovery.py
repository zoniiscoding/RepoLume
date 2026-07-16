"""Non-executing, bounded discovery of supported repository files."""

import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.indexing.failures import IndexingError

SUPPORTED_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".md",
        ".markdown",
        ".txt",
        ".rst",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".ini",
        ".cfg",
    }
)
IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "env",
        "node_modules",
        "site-packages",
        "target",
        "vendor",
        "venv",
    }
)


@dataclass(frozen=True, slots=True)
class DiscoveredFile:
    relative_path: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    files: tuple[DiscoveredFile, ...]
    inspected_file_count: int
    total_bytes: int
    skipped: dict[str, int]


class FileDiscovery:
    """Inspect metadata and a small binary probe without storing file contents."""

    def __init__(self, settings: Settings) -> None:
        self._max_file_bytes = settings.clone_max_file_bytes
        self._max_file_count = settings.clone_max_file_count
        self._max_total_bytes = settings.clone_max_discovered_bytes

    def discover(self, checkout: Path) -> DiscoveryResult:  # noqa: PLR0912
        root = checkout.resolve(strict=True)
        if not root.is_dir():
            raise IndexingError(
                code="unsafe_clone_path",
                message="Repository clone path is unsafe",
                retryable=False,
            )
        skipped: Counter[str] = Counter()
        files: list[DiscoveredFile] = []
        inspected = 0
        total_bytes = 0
        pending = [root]
        while pending:
            directory = pending.pop()
            try:
                entries = tuple(os.scandir(directory))
            except OSError as error:
                raise IndexingError(
                    code="discovery_filesystem_error",
                    message="Repository files could not be inspected",
                    retryable=False,
                ) from error
            for entry in entries:
                path = Path(entry.path)
                self._assert_within(root, path)
                if entry.is_symlink():
                    self._handle_symlink(root, path, skipped)
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in IGNORED_DIRECTORIES:
                        skipped["ignored_directory"] += 1
                    else:
                        pending.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    skipped["special_file"] += 1
                    continue
                inspected += 1
                if inspected > self._max_file_count:
                    raise IndexingError(
                        code="file_count_limit_exceeded",
                        message="Repository exceeds the configured file-count limit",
                        retryable=False,
                    )
                size = entry.stat(follow_symlinks=False).st_size
                if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                    skipped["unsupported_type"] += 1
                    continue
                if size > self._max_file_bytes:
                    skipped["oversized_file"] += 1
                    continue
                if self._is_binary(path):
                    skipped["binary_file"] += 1
                    continue
                if total_bytes + size > self._max_total_bytes:
                    raise IndexingError(
                        code="discovered_bytes_limit_exceeded",
                        message="Repository exceeds the configured discovery-size limit",
                        retryable=False,
                    )
                relative = path.relative_to(root).as_posix()
                files.append(DiscoveredFile(relative_path=relative, size_bytes=size))
                total_bytes += size
        files.sort(key=lambda item: item.relative_path)
        return DiscoveryResult(
            files=tuple(files),
            inspected_file_count=inspected,
            total_bytes=total_bytes,
            skipped=dict(sorted(skipped.items())),
        )

    @staticmethod
    def _assert_within(root: Path, path: Path) -> None:
        if not path.absolute().is_relative_to(root):
            raise IndexingError(
                code="unsafe_repository_path",
                message="Repository contains an unsafe path",
                retryable=False,
            )

    @staticmethod
    def _handle_symlink(root: Path, path: Path, skipped: Counter[str]) -> None:
        try:
            target = path.resolve(strict=False)
        except OSError as error:
            raise IndexingError(
                code="unsafe_symlink",
                message="Repository contains an unsafe symbolic link",
                retryable=False,
            ) from error
        if not target.is_relative_to(root):
            raise IndexingError(
                code="symlink_escape",
                message="Repository contains a symbolic link outside its root",
                retryable=False,
            )
        skipped["symlink"] += 1

    @staticmethod
    def _is_binary(path: Path) -> bool:
        try:
            with path.open("rb") as file:
                return b"\x00" in file.read(8_192)
        except OSError as error:
            raise IndexingError(
                code="discovery_filesystem_error",
                message="Repository files could not be inspected",
                retryable=False,
            ) from error
