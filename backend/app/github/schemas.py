"""Strictly validated subsets of untrusted GitHub API and webhook data."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

_ASCII_CONTROL_LIMIT = 32


class GitHubUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0)
    login: str = Field(min_length=1, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=2048)
    email: str | None = Field(default=None, max_length=320)


class GitHubAccount(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0)
    login: str = Field(min_length=1, max_length=255)
    type: str = Field(pattern=r"^(User|Organization)$")


class GitHubInstallation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0)
    account: GitHubAccount
    permissions: dict[str, str] = Field(default_factory=dict)
    repository_selection: str = Field(pattern=r"^(all|selected)$")
    suspended_at: datetime | None = None


class GitHubRepositoryOwner(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


class GitHubRepository(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(gt=0)
    owner: GitHubRepositoryOwner
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
    full_name: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}/[A-Za-z0-9][A-Za-z0-9._-]{0,254}$"
    )
    html_url: str = Field(pattern=r"^https://github\.com/")
    private: bool
    default_branch: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
    language: str | None = Field(default=None, max_length=64)
    size: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_repository_identity(self) -> "GitHubRepository":
        """Bind every provider field to one fixed github.com repository identity."""
        expected_full_name = f"{self.owner.login}/{self.name}"
        expected_url = f"https://github.com/{expected_full_name}"
        invalid_branch_fragments = ("..", "//", "@{", "\\")
        if self.full_name != expected_full_name or self.html_url.rstrip("/") != expected_url:
            raise ValueError
        if any(item in self.default_branch for item in invalid_branch_fragments):
            raise ValueError
        if self.default_branch.endswith(("/", ".", ".lock")):
            raise ValueError
        return self


class GitHubBranchCommit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class GitHubBranch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=255)
    commit: GitHubBranchCommit


class GitHubOAuthToken(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: SecretStr
    token_type: Literal["bearer"] = "bearer"


class GitHubInstallationToken(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token: SecretStr
    expires_at: datetime


class GitHubCommitIdentity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    date: datetime


class GitHubCommitPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str = Field(min_length=1, max_length=64_000)
    author: GitHubCommitIdentity | None = None
    committer: GitHubCommitIdentity | None = None


class GitHubCommitParent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class GitHubCommitFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    filename: str = Field(min_length=1, max_length=4096)
    status: str = Field(min_length=1, max_length=32)
    patch: str | None = Field(default=None, max_length=256_000)

    @field_validator("filename")
    @classmethod
    def validate_repository_path(cls, value: str) -> str:
        if (
            value.startswith(("/", "\\"))
            or "\\" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or any(ord(character) < _ASCII_CONTROL_LIMIT for character in value)
        ):
            raise ValueError("invalid_repository_path")
        return value


class GitHubCommit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    html_url: str = Field(pattern=r"^https://github\.com/")
    commit: GitHubCommitPayload
    parents: list[GitHubCommitParent] = Field(default_factory=list, max_length=20)
    files: list[GitHubCommitFile] = Field(default_factory=list, max_length=300)
    author: GitHubUser | None = None


class GitHubPullRequestUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    login: str = Field(min_length=1, max_length=255)


class GitHubPullRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    number: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=4096)
    body: str | None = Field(default=None, max_length=256_000)
    state: Literal["open", "closed"]
    html_url: str = Field(pattern=r"^https://github\.com/")
    user: GitHubPullRequestUser | None = None
    merged_at: datetime | None = None
    merge_commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")


class GitHubHistoryBundle(BaseModel):
    """One bounded commit and its directly associated pull requests."""

    model_config = ConfigDict(extra="forbid")

    commit: GitHubCommit
    pull_requests: list[GitHubPullRequest] = Field(default_factory=list, max_length=10)


class GitHubCompareFile(BaseModel):
    """One bounded provider comparison entry; paths remain untrusted until validated."""

    model_config = ConfigDict(extra="ignore")

    filename: str = Field(min_length=1, max_length=4096)
    previous_filename: str | None = Field(default=None, min_length=1, max_length=4096)
    status: Literal["added", "modified", "removed", "renamed", "copied", "changed"]
    changes: int = Field(default=0, ge=0)

    @field_validator("filename", "previous_filename")
    @classmethod
    def validate_compare_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            value.startswith(("/", "\\"))
            or "\\" in value
            or "\x00" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or any(ord(character) < _ASCII_CONTROL_LIMIT for character in value)
        ):
            raise ValueError("invalid_repository_path")
        return value


class GitHubCommitComparison(BaseModel):
    """Bounded authoritative comparison between two server-selected revisions."""

    model_config = ConfigDict(extra="ignore")

    status: Literal["ahead", "behind", "diverged", "identical"]
    ahead_by: int = Field(ge=0)
    behind_by: int = Field(ge=0)
    total_commits: int = Field(ge=0)
    files: list[GitHubCompareFile] = Field(default_factory=list, max_length=3000)
