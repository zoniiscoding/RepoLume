"""Deterministic, fail-closed planning for repository freshness updates."""

from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.db.models.enums import IndexingMode
from app.github.schemas import GitHubCommitComparison

MAX_REPOSITORY_PATH_BYTES = 4096


@dataclass(frozen=True, slots=True)
class ChangedFileSet:
    """Normalized target and removed paths from one trusted GitHub comparison."""

    target_paths: frozenset[str]
    removed_paths: frozenset[str]
    counts: dict[str, int]
    total_changes: int


@dataclass(frozen=True, slots=True)
class FreshnessPlan:
    actual_mode: IndexingMode
    fallback_reason: str | None
    changes: ChangedFileSet


def classify_comparison(comparison: GitHubCommitComparison) -> ChangedFileSet:
    """Classify already schema-validated provider paths in deterministic order."""
    target: set[str] = set()
    removed: set[str] = set()
    counts: Counter[str] = Counter()
    total_changes = 0
    for item in sorted(
        comparison.files,
        key=lambda value: (value.filename, value.previous_filename or "", value.status),
    ):
        _validate_path(item.filename)
        if item.previous_filename is not None:
            _validate_path(item.previous_filename)
        counts[item.status] += 1
        total_changes += item.changes
        if item.status == "removed":
            removed.add(item.filename)
        elif item.status == "renamed":
            if item.previous_filename is None:
                raise ValueError("rename_missing_previous_path")
            removed.add(item.previous_filename)
            target.add(item.filename)
        else:
            target.add(item.filename)
    return ChangedFileSet(
        target_paths=frozenset(target),
        removed_paths=frozenset(removed),
        counts=dict(sorted(counts.items())),
        total_changes=total_changes,
    )


def plan_refresh(  # noqa: PLR0911 -- each fallback reason is an explicit policy branch
    comparison: GitHubCommitComparison | None,
    *,
    has_active_index: bool,
    requested_mode: IndexingMode,
    max_changed_files: int,
) -> FreshnessPlan:
    """Choose incremental work only when comparison completeness is defensible."""
    empty = ChangedFileSet(frozenset(), frozenset(), {}, 0)
    if not has_active_index:
        return FreshnessPlan(IndexingMode.FULL, "missing_active_index", empty)
    if requested_mode is IndexingMode.FULL:
        return FreshnessPlan(IndexingMode.FULL, "requested_full_rebuild", empty)
    if comparison is None:
        return FreshnessPlan(IndexingMode.FULL, "comparison_unavailable", empty)
    if comparison.status in {"behind", "diverged"}:
        return FreshnessPlan(IndexingMode.FULL, "non_fast_forward", empty)
    try:
        changes = classify_comparison(comparison)
    except ValueError:
        return FreshnessPlan(IndexingMode.FULL, "unsafe_comparison", empty)
    if len(comparison.files) >= max_changed_files:
        return FreshnessPlan(IndexingMode.FULL, "comparison_file_limit", changes)
    return FreshnessPlan(IndexingMode.INCREMENTAL, None, changes)


def _validate_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or len(value.encode("utf-8")) > MAX_REPOSITORY_PATH_BYTES
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\x00" in value
        or "\\" in value
    ):
        raise ValueError("unsafe_comparison_path")
