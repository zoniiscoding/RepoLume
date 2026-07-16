"""Hosted LLM request safety, structured validation, and failure classification."""

import asyncio
import json

import httpx
import pytest

from app.llm.client import (
    DeterministicLLMProvider,
    DraftAnswerability,
    GroundedGenerationRequest,
    LLMProviderError,
    OpenAIResponsesClient,
)
from tests.conftest import make_settings


def provider_response(model: str, output: dict[str, object]) -> dict[str, object]:
    return {
        "status": "completed",
        "model": model,
        "output": [
            {
                "type": "message",
                "status": "completed",
                "content": [{"type": "output_text", "text": json.dumps(output)}],
            }
        ],
    }


@pytest.mark.asyncio
async def test_openai_responses_request_is_stateless_bounded_and_structured() -> None:
    settings = make_settings(llm_provider="openai", llm_max_attempts=1)
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]
        captured["request_id"] = request.headers["X-Client-Request-Id"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=provider_response(
                settings.llm_model,
                {
                    "answer": "The service validates the response [E1].",
                    "answerability": "answered",
                    "uncertainty": "low",
                    "evidence_ids": ["E1"],
                },
            ),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url=str(settings.llm_api_url)) as http:
        client = OpenAIResponsesClient(settings, client=http)
        draft = await client.generate(
            GroundedGenerationRequest(
                instructions="fixed-system",
                evidence_payload='{"question":"safe","evidence":[]}',
            )
        )

    assert draft.answerability is DraftAnswerability.ANSWERED
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == settings.llm_model
    assert payload["store"] is False
    assert payload["max_output_tokens"] == settings.llm_max_output_tokens
    assert payload["instructions"] == "fixed-system"
    assert payload["text"]["format"]["type"] == "json_schema"
    assert captured["authorization"] == (f"Bearer {settings.llm_api_key.get_secret_value()}")
    assert settings.llm_api_key.get_secret_value() not in json.dumps(payload)
    assert captured["request_id"] == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"status": "completed", "model": "wrong", "output": []},
        {"status": "incomplete", "model": "gpt-5.4-mini-2026-03-17", "output": []},
        {
            "status": "completed",
            "model": "gpt-5.4-mini-2026-03-17",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "{}"}]}],
        },
        {
            "status": "completed",
            "model": "gpt-5.4-mini-2026-03-17",
            "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}],
        },
    ],
)
async def test_openai_responses_rejects_unusable_or_malformed_output(
    response: dict[str, object],
) -> None:
    settings = make_settings(llm_provider="openai", llm_max_attempts=1)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    async with httpx.AsyncClient(transport=transport, base_url=str(settings.llm_api_url)) as http:
        client = OpenAIResponsesClient(settings, client=http)
        with pytest.raises(LLMProviderError):
            await client.generate(
                GroundedGenerationRequest(instructions="fixed", evidence_payload="{}")
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "code", "retryable"),
    [
        (401, "llm_authentication_failed", False),
        (429, "llm_request_failed", True),
        (400, "llm_request_failed", False),
    ],
)
async def test_openai_responses_classifies_safe_http_failures(
    status_code: int,
    code: str,
    retryable: bool,
) -> None:
    settings = make_settings(llm_provider="openai", llm_max_attempts=1)
    transport = httpx.MockTransport(lambda request: httpx.Response(status_code, text="sensitive"))
    async with httpx.AsyncClient(transport=transport, base_url=str(settings.llm_api_url)) as http:
        client = OpenAIResponsesClient(settings, client=http)
        with pytest.raises(LLMProviderError) as captured:
            await client.generate(
                GroundedGenerationRequest(instructions="fixed", evidence_payload="{}")
            )
    assert captured.value.code == code
    assert captured.value.retryable is retryable
    assert "sensitive" not in str(captured.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [httpx.ConnectError("offline"), httpx.ReadTimeout("slow")])
async def test_openai_responses_retries_transport_failures_without_leaking_details(
    failure: Exception,
) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise failure

    settings = make_settings(
        llm_provider="openai",
        llm_max_attempts=2,
        llm_retry_base_seconds=0.001,
    )
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url=str(settings.llm_api_url)) as http:
        client = OpenAIResponsesClient(settings, client=http)
        with pytest.raises(LLMProviderError) as captured:
            await client.generate(
                GroundedGenerationRequest(instructions="fixed", evidence_payload="{}")
            )
    assert attempts == 2
    assert captured.value.code == "llm_unavailable"
    assert captured.value.retryable is True
    assert "offline" not in str(captured.value)
    assert "slow" not in str(captured.value)


@pytest.mark.asyncio
async def test_openai_responses_propagates_cancellation() -> None:
    started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    settings = make_settings(llm_provider="openai", llm_max_attempts=2)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url=str(settings.llm_api_url)) as http:
        client = OpenAIResponsesClient(settings, client=http)
        task = asyncio.create_task(
            client.generate(GroundedGenerationRequest(instructions="fixed", evidence_payload="{}"))
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_deterministic_provider_answers_only_with_lexically_relevant_evidence() -> None:
    provider = DeterministicLLMProvider()
    relevant_payload = json.dumps(
        {
            "question": "How does fetch_value work?",
            "evidence": [
                {
                    "id": "E1",
                    "file_path": "module.py",
                    "content": "async def fetch_value(): return 1",
                }
            ],
        }
    )
    answered = await provider.generate(
        GroundedGenerationRequest(instructions="fixed", evidence_payload=relevant_payload)
    )
    assert answered.answerability is DraftAnswerability.ANSWERED

    irrelevant_payload = json.dumps(
        {
            "question": "Where is definitely_missing defined?",
            "evidence": [{"id": "E1", "file_path": "module.py", "content": "def existing(): pass"}],
        }
    )
    refused = await provider.generate(
        GroundedGenerationRequest(instructions="fixed", evidence_payload=irrelevant_payload)
    )
    assert refused.answerability is DraftAnswerability.INSUFFICIENT_EVIDENCE
    assert refused.evidence_ids == []
