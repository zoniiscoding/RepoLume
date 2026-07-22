"""Safe clone command and non-executing discovery security tests."""

import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.indexing.clone import (
    ClonedRepository,
    CloneRequest,
    GitHubRepositoryCloner,
    _apply_resource_limits,
)
from app.indexing.discovery import FileDiscovery
from app.indexing.failures import IndexingError
from tests.conftest import make_settings

TOKEN = "installation-token-sentinel"


def assert_directory_empty(path: Path) -> None:
    assert tuple(path.iterdir()) == ()


def clone_request(**overrides: str) -> CloneRequest:
    values = {
        "owner": "octo-org",
        "name": "safe-repository",
        "default_branch": "main",
    }
    values.update(overrides)
    return CloneRequest(
        owner=values["owner"],
        name=values["name"],
        default_branch=values["default_branch"],
        installation_token=SecretStr(TOKEN),
    )


def test_clone_command_is_fixed_shallow_and_contains_no_token(tmp_path: Path) -> None:
    cloner = GitHubRepositoryCloner(make_settings(), temp_root=tmp_path)
    command = cloner.command_for(clone_request(), tmp_path / "checkout")

    assert command[0] == "/usr/bin/git"
    assert "--depth=1" in command
    assert "--single-branch" in command
    assert "--no-recurse-submodules" in command
    assert "core.hooksPath=/dev/null" in command
    assert "protocol.file.allow=never" in command
    assert "https://github.com/octo-org/safe-repository.git" in command
    assert TOKEN not in " ".join(command)


@pytest.mark.parametrize(
    "overrides",
    [
        {"owner": "--upload-pack=evil"},
        {"name": "../escape"},
        {"default_branch": "--config=evil"},
        {"default_branch": "refs/../escape"},
        {"default_branch": "feature@{1"},
    ],
)
def test_clone_rejects_argument_injection(overrides: dict[str, str], tmp_path: Path) -> None:
    cloner = GitHubRepositoryCloner(make_settings(), temp_root=tmp_path)

    with pytest.raises(IndexingError) as captured:
        cloner.command_for(clone_request(**overrides), tmp_path / "checkout")

    assert captured.value.retryable is False


class FakeProcess:
    def __init__(self, *, return_code: int = 0, wait_forever: bool = False) -> None:
        self.returncode: int | None = None
        self.pid = 999_999
        self._return_code = return_code
        self._wait_forever = wait_forever

    async def wait(self) -> int:
        if self._wait_forever:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                self._wait_forever = False
                raise
        self.returncode = self._return_code
        return self._return_code


@pytest.mark.asyncio
async def test_clone_uses_secret_environment_and_always_cleans_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_subprocess(*args: str, **kwargs: Any) -> FakeProcess:
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return FakeProcess()

    cloner = GitHubRepositoryCloner(make_settings(), temp_root=tmp_path)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(cloner, "_read_commit", AsyncMock(return_value="a" * 40))

    cloned = await cloner.clone(clone_request())

    assert TOKEN not in " ".join(captured["args"])
    assert captured["env"]["REPOLU_GIT_TOKEN"] == TOKEN
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert (cloned.workspace / "git-askpass").stat().st_mode & 0o777 == 0o700
    cloner.cleanup(cloned)
    assert_directory_empty(tmp_path)


@pytest.mark.asyncio
async def test_public_clone_uses_no_credential_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_subprocess(*args: str, **kwargs: Any) -> FakeProcess:
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return FakeProcess()

    request = clone_request()
    public_request = CloneRequest(
        owner=request.owner,
        name=request.name,
        default_branch=request.default_branch,
        installation_token=None,
    )
    cloner = GitHubRepositoryCloner(make_settings(), temp_root=tmp_path)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(cloner, "_read_commit", AsyncMock(return_value="a" * 40))

    cloned = await cloner.clone(public_request)

    assert captured["env"]["GIT_ASKPASS"] == "/bin/false"
    assert "REPOLU_GIT_TOKEN" not in captured["env"]
    assert TOKEN not in " ".join(captured["args"])
    cloner.cleanup(cloned)
    assert_directory_empty(tmp_path)


@pytest.mark.asyncio
async def test_clone_failure_and_timeout_remove_temporary_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    processes = [FakeProcess(return_code=1), FakeProcess(wait_forever=True)]

    async def fake_subprocess(*args: str, **kwargs: Any) -> FakeProcess:
        del args, kwargs
        return processes.pop(0)

    cloner = GitHubRepositoryCloner(make_settings(), temp_root=tmp_path)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

    with pytest.raises(IndexingError, match="clone_failed"):
        await cloner.clone(clone_request())
    assert_directory_empty(tmp_path)

    cast(Any, cloner)._timeout_seconds = 0.001
    with pytest.raises(IndexingError, match="clone_timeout"):
        await cloner.clone(clone_request())
    assert_directory_empty(tmp_path)


@pytest.mark.asyncio
async def test_clone_rejects_excessive_repository_size(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_subprocess(*args: str, **kwargs: Any) -> FakeProcess:
        del args, kwargs
        return FakeProcess()

    cloner = GitHubRepositoryCloner(
        make_settings(clone_max_repository_bytes=1024),
        temp_root=tmp_path,
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(cloner, "_directory_size", lambda _: 1025)

    with pytest.raises(IndexingError, match="repository_too_large"):
        await cloner.clone(clone_request())

    assert_directory_empty(tmp_path)


def test_resource_limits_are_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    applied: list[tuple[int, tuple[int, int]]] = []
    monkeypatch.setattr(
        "app.indexing.clone.resource.setrlimit",
        lambda name, limits: applied.append((name, limits)),
    )

    _apply_resource_limits(cpu_seconds=10, memory_bytes=20, file_bytes=30)

    assert len(applied) == 4
    assert (10, 10) in {limits for _, limits in applied}
    assert (20, 20) in {limits for _, limits in applied}
    assert (30, 30) in {limits for _, limits in applied}


def test_clone_cleanup_failure_is_explicit_and_never_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "clone"
    workspace.mkdir()
    cloned = ClonedRepository(workspace, workspace, "a" * 40)
    monkeypatch.setattr("app.indexing.clone.shutil.rmtree", lambda _: None)

    with pytest.raises(IndexingError, match="clone_cleanup_failed") as captured:
        GitHubRepositoryCloner.cleanup(cloned)

    assert captured.value.retryable is True
    assert workspace.exists()


def test_clone_cleanup_wraps_filesystem_errors_without_path_disclosure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "private-repository-name"
    workspace.mkdir()
    cloned = ClonedRepository(workspace, workspace, "a" * 40)

    def fail_cleanup(_: Path) -> None:
        raise PermissionError("private filesystem detail")

    monkeypatch.setattr("app.indexing.clone.shutil.rmtree", fail_cleanup)
    with pytest.raises(IndexingError) as captured:
        GitHubRepositoryCloner.cleanup(cloned)

    assert captured.value.code == "clone_cleanup_failed"
    assert "private" not in captured.value.safe_message.casefold()


def test_discovery_filters_without_reading_or_executing_repository(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def safe():\n    return 1\n")
    (tmp_path / "README.md").write_text("# Safe\n")
    (tmp_path / "image.png").write_bytes(b"not inspected")
    (tmp_path / "binary.py").write_bytes(b"safe\x00binary")
    (tmp_path / "large.py").write_bytes(b"x" * 1025)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.py").write_text("raise RuntimeError()")
    marker = tmp_path / "execution-marker"
    (tmp_path / "danger.py").write_text(f"open({str(marker)!r}, 'w').write('bad')")

    result = FileDiscovery(
        make_settings(clone_max_file_bytes=1024, parser_max_input_bytes=1024)
    ).discover(tmp_path)

    assert [item.relative_path for item in result.files] == [
        "README.md",
        "danger.py",
        "src/app.py",
    ]
    assert result.skipped == {
        "binary_file": 1,
        "ignored_directory": 1,
        "oversized_file": 1,
        "unsupported_type": 1,
    }
    assert not marker.exists()


def test_discovery_rejects_file_count_and_total_byte_limits(tmp_path: Path) -> None:
    (tmp_path / "one.py").write_bytes(b"x" * 600)
    (tmp_path / "two.py").write_bytes(b"x" * 600)

    with pytest.raises(IndexingError, match="file_count_limit_exceeded"):
        FileDiscovery(make_settings(clone_max_file_count=1)).discover(tmp_path)

    with pytest.raises(IndexingError, match="discovered_bytes_limit_exceeded"):
        FileDiscovery(make_settings(clone_max_discovered_bytes=1024)).discover(tmp_path)


def test_discovery_rejects_symlink_escape_and_skips_internal_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    target.write_text("safe = True")
    internal = tmp_path / "internal.py"
    internal.symlink_to(target)

    result = FileDiscovery(make_settings()).discover(tmp_path)
    assert result.skipped["symlink"] == 1

    internal.unlink()
    external = tmp_path / "external.py"
    external.symlink_to(Path("/private/tmp/outside.py"))
    with pytest.raises(IndexingError, match="symlink_escape"):
        FileDiscovery(make_settings()).discover(tmp_path)


def test_discovery_rejects_missing_clone_root(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        FileDiscovery(make_settings()).discover(tmp_path / "missing")
