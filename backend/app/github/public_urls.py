"""Strict parser for user-supplied public GitHub repository URLs."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_REPOSITORY_URL_LENGTH = 2048
ASCII_CONTROL_LIMIT = 32
EXPECTED_PATH_PARTS = 3
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


class PublicRepositoryURLError(ValueError):
    """The URL is not one unambiguous HTTPS github.com repository identity."""


@dataclass(frozen=True, slots=True)
class PublicRepositoryURL:
    owner: str
    repository: str

    @property
    def canonical_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repository}"


def parse_public_repository_url(value: str) -> PublicRepositoryURL:
    """Reject alternate schemes/hosts, credentials, ports, suffix paths, and encoding tricks."""
    if (
        not value
        or len(value) > MAX_REPOSITORY_URL_LENGTH
        or value != value.strip()
        or any(ord(character) < ASCII_CONTROL_LIMIT for character in value)
        or "%" in value
        or "\\" in value
    ):
        raise PublicRepositoryURLError
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise PublicRepositoryURLError from error
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.netloc.lower() != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PublicRepositoryURLError
    path = parsed.path[:-1] if parsed.path.endswith("/") else parsed.path
    parts = path.split("/")
    if len(parts) != EXPECTED_PATH_PARTS or parts[0] != "" or not parts[1] or not parts[2]:
        raise PublicRepositoryURLError
    owner, repository = parts[1], parts[2]
    if repository.endswith(".git"):
        repository = repository[:-4]
    if (
        not repository
        or owner in {".", ".."}
        or repository in {".", ".."}
        or _SEGMENT.fullmatch(owner) is None
        or _SEGMENT.fullmatch(repository) is None
    ):
        raise PublicRepositoryURLError
    return PublicRepositoryURL(owner=owner, repository=repository)
