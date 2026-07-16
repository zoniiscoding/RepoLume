"""Fixed-command, resource-bounded GitHub repository cloning."""

import asyncio
import os
import re
import resource
import shutil
import signal
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import SecretStr

from app.core.config import Settings
from app.indexing.failures import IndexingError

IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
BRANCH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")


@dataclass(frozen=True, slots=True)
class CloneRequest:
    owner: str
    name: str
    default_branch: str
    installation_token: SecretStr


@dataclass(frozen=True, slots=True)
class ClonedRepository:
    workspace: Path
    checkout: Path
    commit_sha: str


class RepositoryClonerProtocol(Protocol):
    async def clone(self, request: CloneRequest) -> ClonedRepository: ...

    def cleanup(self, cloned: ClonedRepository) -> None: ...


def _validate_identity(value: str, *, field: str) -> str:
    if not IDENTITY_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise IndexingError(
            code="invalid_repository_identity",
            message=f"GitHub {field} is invalid",
            retryable=False,
        )
    return value


def _validate_branch(value: str) -> str:
    invalid_fragments = ("..", "//", "@{", "\\")
    if (
        not BRANCH_PATTERN.fullmatch(value)
        or any(fragment in value for fragment in invalid_fragments)
        or value.endswith(("/", ".", ".lock"))
    ):
        raise IndexingError(
            code="invalid_default_branch",
            message="GitHub default branch is invalid",
            retryable=False,
        )
    return value


def _apply_resource_limits(
    *,
    cpu_seconds: int,
    memory_bytes: int,
    file_bytes: int,
) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))


class GitHubRepositoryCloner:
    """Clone only a validated github.com identity with no credential in argv."""

    def __init__(self, settings: Settings, *, temp_root: Path | None = None) -> None:
        self._git = settings.clone_git_executable
        self._timeout_seconds = settings.clone_timeout_seconds
        self._max_repository_bytes = settings.clone_max_repository_bytes
        self._memory_bytes = settings.clone_process_memory_bytes
        self._cpu_seconds = settings.clone_process_cpu_seconds
        self._temp_root = temp_root

    def command_for(self, request: CloneRequest, checkout: Path) -> tuple[str, ...]:
        owner = _validate_identity(request.owner, field="owner")
        name = _validate_identity(request.name, field="repository name")
        branch = _validate_branch(request.default_branch)
        remote = f"https://github.com/{owner}/{name}.git"
        return (
            str(self._git),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "init.templateDir=",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "submodule.recurse=false",
            "-c",
            "filter.lfs.required=false",
            "-c",
            "filter.lfs.smudge=",
            "-c",
            "filter.lfs.process=",
            "clone",
            "--depth=1",
            "--single-branch",
            "--no-tags",
            "--no-recurse-submodules",
            "--branch",
            branch,
            "--",
            remote,
            str(checkout),
        )

    async def clone(self, request: CloneRequest) -> ClonedRepository:
        workspace = await asyncio.to_thread(self._prepare_workspace)
        checkout = workspace / "checkout"
        askpass = workspace / "git-askpass"
        try:
            self._write_askpass(askpass)
            command = self.command_for(request, checkout)
            environment = self._environment(
                askpass=askpass,
                token=request.installation_token.get_secret_value(),
            )
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=environment,
                start_new_session=True,
                preexec_fn=lambda: _apply_resource_limits(
                    cpu_seconds=self._cpu_seconds,
                    memory_bytes=self._memory_bytes,
                    file_bytes=self._max_repository_bytes,
                ),
            )
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    return_code = await process.wait()
            except TimeoutError as error:
                self._terminate(process)
                await process.wait()
                raise IndexingError(
                    code="clone_timeout",
                    message="Repository clone timed out",
                    retryable=True,
                ) from error
            self._validate_return_code(return_code)
            self._assert_within(workspace, checkout)
            self._validate_repository_size(workspace)
            commit_sha = await self._read_commit(checkout)
            return ClonedRepository(
                workspace=workspace,
                checkout=checkout.resolve(),
                commit_sha=commit_sha,
            )
        except BaseException:
            shutil.rmtree(workspace, ignore_errors=True)
            raise

    @staticmethod
    def _write_askpass(path: Path) -> None:
        script = (
            b"#!/bin/sh\n"
            b'case "$1" in\n'
            b"  *Username*) printf '%s\\n' 'x-access-token' ;;\n"
            b"  *) printf '%s\\n' \"$REPOLU_GIT_TOKEN\" ;;\n"
            b"esac\n"
        )
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        try:
            os.write(descriptor, script)
        finally:
            os.close(descriptor)

    @staticmethod
    def _environment(*, askpass: Path, token: str) -> dict[str, str]:
        return {
            "HOME": str(askpass.parent),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "GIT_ASKPASS": str(askpass),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "REPOLU_GIT_TOKEN": token,
        }

    async def _read_commit(self, checkout: Path) -> str:
        process = await asyncio.create_subprocess_exec(
            str(self._git),
            "-C",
            str(checkout),
            "rev-parse",
            "--verify",
            "HEAD",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={
                "HOME": str(checkout.parent),
                "PATH": "/usr/bin:/bin",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
            },
            start_new_session=True,
            preexec_fn=lambda: _apply_resource_limits(
                cpu_seconds=min(self._cpu_seconds, 10),
                memory_bytes=self._memory_bytes,
                file_bytes=self._max_repository_bytes,
            ),
        )
        try:
            async with asyncio.timeout(min(self._timeout_seconds, 10)):
                stdout, _ = await process.communicate()
        except TimeoutError as error:
            self._terminate(process)
            await process.wait()
            raise IndexingError(
                code="clone_revision_timeout",
                message="Cloned repository revision inspection timed out",
                retryable=True,
            ) from error
        commit_sha = stdout.decode("ascii", errors="ignore").strip()
        if process.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", commit_sha):
            raise IndexingError(
                code="clone_revision_invalid",
                message="Cloned repository revision is invalid",
                retryable=False,
            )
        return commit_sha

    @staticmethod
    def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)

    @staticmethod
    def _assert_within(workspace: Path, checkout: Path) -> None:
        if not checkout.resolve().is_relative_to(workspace):
            raise IndexingError(
                code="unsafe_clone_path",
                message="Repository clone path is unsafe",
                retryable=False,
            )

    @staticmethod
    def _directory_size(root: Path) -> int:
        total = 0
        for directory, _, filenames in os.walk(root, followlinks=False):
            for filename in filenames:
                try:
                    total += (Path(directory) / filename).lstat().st_size
                except OSError as error:
                    raise IndexingError(
                        code="clone_filesystem_error",
                        message="Repository clone could not be inspected",
                        retryable=False,
                    ) from error
        return total

    def _prepare_workspace(self) -> Path:
        workspace = Path(tempfile.mkdtemp(prefix="repolume-clone-", dir=self._temp_root)).resolve()
        workspace.chmod(0o700)
        return workspace

    @staticmethod
    def _validate_return_code(return_code: int) -> None:
        if return_code != 0:
            raise IndexingError(
                code="clone_failed",
                message="Repository clone failed",
                retryable=True,
            )

    def _validate_repository_size(self, workspace: Path) -> None:
        if self._directory_size(workspace) > self._max_repository_bytes:
            raise IndexingError(
                code="repository_too_large",
                message="Repository exceeds the configured size limit",
                retryable=False,
            )

    @staticmethod
    def cleanup(cloned: ClonedRepository) -> None:
        shutil.rmtree(cloned.workspace, ignore_errors=True)
