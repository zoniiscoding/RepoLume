"""Authentication token, PKCE, cookie, and bearer regression tests."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.auth.tokens import AccessTokenError, TokenService
from tests.conftest import make_settings


def test_oauth_state_and_pkce_values_are_high_entropy_and_hash_only() -> None:
    service = TokenService(make_settings())

    credentials = service.new_oauth_credentials()

    assert len(credentials.state) >= 40
    assert len(credentials.code_verifier) >= 43
    assert len(credentials.code_challenge) == 43
    assert credentials.state not in credentials.state_hash
    assert credentials.code_verifier not in credentials.code_verifier_hash
    assert service.hash_opaque_token(credentials.state) == credentials.state_hash


def test_access_token_round_trip_and_tamper_rejection() -> None:
    service = TokenService(make_settings())
    user_id = uuid.uuid4()

    token = service.issue_access_token(user_id)

    assert token.expires_in == 900
    assert service.decode_access_token(token.value) == user_id
    with pytest.raises(AccessTokenError):
        service.decode_access_token(token.value + "tampered")


def test_expired_access_token_is_rejected() -> None:
    service = TokenService(make_settings())
    token = service.issue_access_token(
        uuid.uuid4(),
        now=datetime.now(UTC) - timedelta(hours=1),
    )

    with pytest.raises(AccessTokenError):
        service.decode_access_token(token.value)


def test_refresh_tokens_are_random_and_persist_only_as_keyed_hashes() -> None:
    service = TokenService(make_settings())

    first_raw, first_hash = service.new_refresh_token()
    second_raw, second_hash = service.new_refresh_token()

    assert first_raw != second_raw
    assert first_hash != second_hash
    assert first_raw not in first_hash
    assert len(first_hash) == 64
