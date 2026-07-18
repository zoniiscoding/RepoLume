"""Changed-file normalization and incremental/full fallback policy."""

import pytest

from app.db.models.enums import IndexingMode
from app.github.schemas import GitHubCommitComparison
from app.indexing.freshness import classify_comparison, plan_refresh


def comparison(
    *,
    status: str = "ahead",
    files: list[dict[str, object]] | None = None,
) -> GitHubCommitComparison:
    return GitHubCommitComparison.model_validate(
        {
            "status": status,
            "ahead_by": 1 if status == "ahead" else 0,
            "behind_by": 1 if status == "behind" else 0,
            "total_commits": 1,
            "files": files or [],
        }
    )


def test_changed_files_classify_add_modify_delete_rename_and_copy() -> None:
    result = classify_comparison(
        comparison(
            files=[
                {"filename": "src/new.py", "status": "added", "changes": 5},
                {"filename": "src/service.py", "status": "modified", "changes": 2},
                {"filename": "src/gone.py", "status": "removed", "changes": 7},
                {
                    "filename": "src/moved.py",
                    "previous_filename": "src/old.py",
                    "status": "renamed",
                    "changes": 1,
                },
                {"filename": "docs/copy.md", "status": "copied", "changes": 3},
            ]
        )
    )

    assert result.target_paths == frozenset(
        {"src/new.py", "src/service.py", "src/moved.py", "docs/copy.md"}
    )
    assert result.removed_paths == frozenset({"src/gone.py", "src/old.py"})
    assert result.counts == {
        "added": 1,
        "copied": 1,
        "modified": 1,
        "removed": 1,
        "renamed": 1,
    }


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "../escape.py", "src/../../escape.py", "src\\evil.py", "bad\x00.py"],
)
def test_compare_schema_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError, match="invalid_repository_path"):
        comparison(files=[{"filename": path, "status": "modified"}])


def test_unicode_paths_are_valid_and_ordering_is_deterministic() -> None:
    first = classify_comparison(
        comparison(
            files=[
                {"filename": "文档/说明.md", "status": "modified"},
                {"filename": "src/a.py", "status": "added"},
            ]
        )
    )
    second = classify_comparison(
        comparison(
            files=[
                {"filename": "src/a.py", "status": "added"},
                {"filename": "文档/说明.md", "status": "modified"},
            ]
        )
    )
    assert first == second


def test_rename_requires_previous_path() -> None:
    with pytest.raises(ValueError, match="rename_missing_previous_path"):
        classify_comparison(comparison(files=[{"filename": "src/new.py", "status": "renamed"}]))


@pytest.mark.parametrize(
    ("has_active", "requested", "provider_status", "expected_reason"),
    [
        (False, IndexingMode.INCREMENTAL, "ahead", "missing_active_index"),
        (True, IndexingMode.FULL, "ahead", "requested_full_rebuild"),
        (True, IndexingMode.INCREMENTAL, None, "comparison_unavailable"),
        (True, IndexingMode.INCREMENTAL, "behind", "non_fast_forward"),
        (True, IndexingMode.INCREMENTAL, "diverged", "non_fast_forward"),
    ],
)
def test_full_rebuild_fallback_rules(
    has_active: bool,
    requested: IndexingMode,
    provider_status: str | None,
    expected_reason: str,
) -> None:
    provider = None if provider_status is None else comparison(status=provider_status)
    plan = plan_refresh(
        provider,
        has_active_index=has_active,
        requested_mode=requested,
        max_changed_files=300,
    )
    assert plan.actual_mode is IndexingMode.FULL
    assert plan.fallback_reason == expected_reason


def test_clean_ahead_comparison_selects_incremental_and_limit_falls_back() -> None:
    provider = comparison(files=[{"filename": "src/app.py", "status": "modified"}])
    incremental = plan_refresh(
        provider,
        has_active_index=True,
        requested_mode=IndexingMode.INCREMENTAL,
        max_changed_files=300,
    )
    limited = plan_refresh(
        provider,
        has_active_index=True,
        requested_mode=IndexingMode.INCREMENTAL,
        max_changed_files=1,
    )
    assert incremental.actual_mode is IndexingMode.INCREMENTAL
    assert incremental.fallback_reason is None
    assert limited.actual_mode is IndexingMode.FULL
    assert limited.fallback_reason == "comparison_file_limit"
