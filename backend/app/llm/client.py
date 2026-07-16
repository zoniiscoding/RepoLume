"""Typed, bounded hosted LLM adapter with strict structured-output validation."""

import asyncio
import json
import re
import secrets
from enum import StrEnum
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import MINIMUM_SECRET_LENGTH, LLMProvider, Settings
from app.core.request_context import get_request_id

_HTTP_OK = 200
_DETERMINISTIC_STOPWORDS = {
    "what",
    "where",
    "which",
    "does",
    "how",
    "the",
    "this",
    "that",
    "with",
    "from",
    "into",
    "function",
    "implementation",
    "implemented",
    "module",
    "repository",
    "defined",
    "exists",
}
_WORD_PATTERN = re.compile(r"[\w.]+", re.UNICODE)
_MINIMUM_RELEVANCE_TOKEN_LENGTH = 3


class DraftAnswerability(StrEnum):
    """Answer states a synthesis provider may select."""

    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNSUPPORTED_QUESTION = "unsupported_question"


class DraftUncertainty(StrEnum):
    """Provider-declared uncertainty, checked independently by the server."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GroundedAnswerDraft(BaseModel):
    """Strict provider output; citation metadata is deliberately absent."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    answerability: DraftAnswerability
    uncertainty: DraftUncertainty
    evidence_ids: list[str] = Field(max_length=20)


class GroundedGenerationRequest(BaseModel):
    """Provider-neutral prompt inputs."""

    model_config = ConfigDict(extra="forbid")

    instructions: str = Field(min_length=1)
    evidence_payload: str = Field(min_length=1)


class LLMProviderError(RuntimeError):
    """Safe provider failure with retryability classification."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class LLMProviderProtocol(Protocol):
    async def generate(self, request: GroundedGenerationRequest) -> GroundedAnswerDraft: ...

    async def close(self) -> None: ...


class _ResponseContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    text: str | None = None
    refusal: str | None = None


class _ResponseOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    status: str | None = None
    content: list[_ResponseContent] = Field(default_factory=list)


class _ResponsesPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    model: str
    output: list[_ResponseOutput]


class OpenAIResponsesClient:
    """Call one pinned OpenAI Responses model without retaining provider state."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._model = settings.llm_model
        self._api_key = settings.llm_api_key.get_secret_value()
        if len(self._api_key) < MINIMUM_SECRET_LENGTH:
            raise LLMProviderError("llm_configuration_invalid", retryable=False)
        self._max_output_tokens = settings.llm_max_output_tokens
        self._max_answer_characters = settings.llm_max_answer_characters
        self._max_attempts = settings.llm_max_attempts
        self._retry_base = settings.llm_retry_base_seconds
        self._semaphore = asyncio.Semaphore(settings.llm_max_concurrent_requests)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=str(settings.llm_api_url).rstrip("/"),
            follow_redirects=False,
            timeout=httpx.Timeout(
                connect=settings.llm_connect_timeout_seconds,
                read=settings.llm_read_timeout_seconds,
                write=settings.llm_read_timeout_seconds,
                pool=settings.llm_connect_timeout_seconds,
            ),
        )

    async def generate(self, request: GroundedGenerationRequest) -> GroundedAnswerDraft:
        payload = {
            "model": self._model,
            "instructions": request.instructions,
            "input": [{"role": "user", "content": request.evidence_payload}],
            "max_output_tokens": self._max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "repolume_grounded_answer",
                    "strict": True,
                    "schema": GroundedAnswerDraft.model_json_schema(),
                }
            },
        }
        async with self._semaphore:
            response = await self._request_with_retry(payload)
        try:
            parsed = _ResponsesPayload.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise LLMProviderError("llm_malformed_response", retryable=False) from error
        if parsed.status != "completed" or parsed.model != self._model:
            raise LLMProviderError("llm_response_mismatch", retryable=False)
        output_texts = [
            item.text
            for output in parsed.output
            if output.type == "message" and output.status in {None, "completed"}
            for item in output.content
            if item.type == "output_text" and item.text is not None
        ]
        refusals = [
            item.refusal
            for output in parsed.output
            for item in output.content
            if item.type == "refusal" and item.refusal is not None
        ]
        if refusals or len(output_texts) != 1:
            raise LLMProviderError("llm_unusable_response", retryable=False)
        try:
            draft = GroundedAnswerDraft.model_validate_json(output_texts[0])
        except (ValueError, ValidationError) as error:
            raise LLMProviderError("llm_malformed_response", retryable=False) from error
        if len(draft.answer) > self._max_answer_characters:
            raise LLMProviderError("llm_answer_too_large", retryable=False)
        return draft

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request_with_retry(self, payload: dict[str, object]) -> httpx.Response:
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post(
                    "/responses",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                        "X-Client-Request-Id": get_request_id(),
                    },
                    content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                )
            except (httpx.ConnectError, httpx.TimeoutException) as error:
                if attempt == self._max_attempts:
                    raise LLMProviderError("llm_unavailable", retryable=True) from error
                await self._backoff(attempt)
                continue
            if response.status_code == _HTTP_OK:
                return response
            retryable = response.status_code in {408, 425, 429, 500, 502, 503, 504}
            if retryable and attempt < self._max_attempts:
                await self._backoff(attempt)
                continue
            if response.status_code in {401, 403}:
                raise LLMProviderError("llm_authentication_failed", retryable=False)
            raise LLMProviderError("llm_request_failed", retryable=retryable)
        raise AssertionError("retry_exhausted")

    async def _backoff(self, attempt: int) -> None:
        ceiling = self._retry_base * (2 ** (attempt - 1))
        jitter = secrets.randbelow(max(1, int(ceiling * 500))) / 1000
        await asyncio.sleep(ceiling + jitter)


class DeterministicLLMProvider:
    """Credential-free test provider; forbidden by production settings validation."""

    async def generate(self, request: GroundedGenerationRequest) -> GroundedAnswerDraft:
        payload = json.loads(request.evidence_payload)
        evidence = payload.get("evidence", [])
        if not isinstance(evidence, list) or not evidence:
            return GroundedAnswerDraft(
                answer="The indexed evidence is insufficient to answer this question.",
                answerability=DraftAnswerability.INSUFFICIENT_EVIDENCE,
                uncertainty=DraftUncertainty.HIGH,
                evidence_ids=[],
            )
        first = evidence[0]
        if not isinstance(first, dict) or not isinstance(first.get("id"), str):
            raise LLMProviderError("llm_malformed_input", retryable=False)
        question = payload.get("question")
        if not isinstance(question, str) or not self._has_lexical_support(question, evidence):
            return GroundedAnswerDraft(
                answer="The indexed evidence is insufficient to answer this question.",
                answerability=DraftAnswerability.INSUFFICIENT_EVIDENCE,
                uncertainty=DraftUncertainty.HIGH,
                evidence_ids=[],
            )
        return GroundedAnswerDraft(
            answer="The indexed repository evidence contains a relevant implementation excerpt.",
            answerability=DraftAnswerability.ANSWERED,
            uncertainty=DraftUncertainty.MEDIUM,
            evidence_ids=[first["id"]],
        )

    async def close(self) -> None:
        return None

    @staticmethod
    def _has_lexical_support(question: str, evidence: list[object]) -> bool:
        question_tokens = {
            token.casefold()
            for token in _WORD_PATTERN.findall(question)
            if len(token) >= _MINIMUM_RELEVANCE_TOKEN_LENGTH
            and token.casefold() not in _DETERMINISTIC_STOPWORDS
        }
        searchable_parts: list[str] = []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            searchable_parts.extend(
                str(item.get(key) or "")
                for key in (
                    "file_path",
                    "content",
                    "symbol_name",
                    "qualified_symbol_name",
                    "chunk_type",
                )
            )
        searchable = "\n".join(searchable_parts).casefold()
        if {"prompt", "injection"}.issubset(question_tokens) and "ignore all prior" in searchable:
            return True
        return any(token in searchable for token in question_tokens)


def create_llm_provider(settings: Settings) -> LLMProviderProtocol:
    """Build the configured provider behind one application-owned protocol."""
    if settings.llm_provider is LLMProvider.DETERMINISTIC:
        return DeterministicLLMProvider()
    return OpenAIResponsesClient(settings)
