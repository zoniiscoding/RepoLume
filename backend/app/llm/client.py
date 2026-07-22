import asyncio
import json
import re
import secrets
from enum import StrEnum
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.models import (
    AgentDecision,
    AgentGenerationRequest,
    AgentToolArguments,
    AgentToolName,
)
from app.core.config import (
    GEMINI_API_BASE_URL,
    MINIMUM_SECRET_LENGTH,
    LLMProvider,
    Settings,
)
from app.core.request_context import get_request_id
from app.rag.models import Answerability, AnswerUncertainty

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


class _ChatCompletionMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str | None = None
    refusal: str | None = None


class _ChatCompletionChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int | None = None
    finish_reason: str | None = None
    message: _ChatCompletionMessage


class _ChatCompletionsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str | None = None
    choices: list[_ChatCompletionChoice] = Field(default_factory=list)


class OpenAIResponsesClient:
    """Call one pinned OpenAI Responses model without retaining provider state."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._model = settings.llm_model
        self._api_key = settings.llm_api_key.get_secret_value()
        if len(self._api_key) < MINIMUM_SECRET_LENGTH:
            raise LLMProviderError("llm_configuration_invalid", retryable=False)
        self._max_output_tokens = settings.llm_max_output_tokens
        self._agent_max_output_tokens = settings.agent_max_final_output_tokens
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

    async def decide(self, request: AgentGenerationRequest) -> AgentDecision:
        """Select one typed tool call or final response through the same hosted boundary."""
        payload = {
            "model": self._model,
            "instructions": request.instructions,
            "input": [{"role": "user", "content": request.context_payload}],
            "max_output_tokens": self._agent_max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "repolume_agent_decision",
                    "strict": True,
                    "schema": self._agent_decision_schema(),
                }
            },
        }
        async with self._semaphore:
            response = await self._request_with_retry(payload)
        output_text = self._validated_output_text(response)
        try:
            decision = AgentDecision.model_validate_json(output_text)
        except (ValueError, ValidationError) as error:
            raise LLMProviderError("llm_malformed_response", retryable=False) from error
        if decision.answer is not None and len(decision.answer) > self._max_answer_characters:
            raise LLMProviderError("llm_answer_too_large", retryable=False)
        return decision

    @staticmethod
    def _agent_decision_schema() -> dict[str, object]:
        schema = AgentDecision.model_json_schema()
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise LLMProviderError("llm_schema_invalid", retryable=False)
        schema["required"] = list(properties)
        return schema

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

    def _validated_output_text(self, response: httpx.Response) -> str:
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
        return output_texts[0]

    async def _backoff(self, attempt: int) -> None:
        ceiling = self._retry_base * (2 ** (attempt - 1))
        jitter = secrets.randbelow(max(1, int(ceiling * 500))) / 1000
        await asyncio.sleep(ceiling + jitter)


class GeminiChatCompletionsClient:
    """Call Gemini through Google's OpenAI-compatible Chat Completions endpoint."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._model = settings.llm_model
        self._api_key = settings.llm_api_key.get_secret_value()
        if len(self._api_key) < MINIMUM_SECRET_LENGTH:
            raise LLMProviderError("llm_configuration_invalid", retryable=False)
        self._max_output_tokens = settings.llm_max_output_tokens
        self._agent_max_output_tokens = settings.agent_max_final_output_tokens
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
        payload = self._structured_chat_payload(
            instructions=request.instructions,
            user_content=request.evidence_payload,
            max_tokens=self._max_output_tokens,
            schema_name="repolume_grounded_answer",
            schema=GroundedAnswerDraft.model_json_schema(),
        )
        async with self._semaphore:
            response = await self._request_with_retry(payload)
        output_text = self._validated_output_text(response)
        try:
            draft = GroundedAnswerDraft.model_validate_json(output_text)
        except (ValueError, ValidationError) as error:
            raise LLMProviderError("llm_malformed_response", retryable=False) from error
        if len(draft.answer) > self._max_answer_characters:
            raise LLMProviderError("llm_answer_too_large", retryable=False)
        return draft

    async def decide(self, request: AgentGenerationRequest) -> AgentDecision:
        """Select one typed tool call or final response through Gemini."""
        payload = self._structured_chat_payload(
            instructions=request.instructions,
            user_content=request.context_payload,
            max_tokens=self._agent_max_output_tokens,
            schema_name="repolume_agent_decision",
            schema=self._agent_decision_schema(),
        )
        async with self._semaphore:
            response = await self._request_with_retry(payload)
        output_text = self._validated_output_text(response)
        try:
            decision = AgentDecision.model_validate_json(output_text)
        except (ValueError, ValidationError) as error:
            raise LLMProviderError("llm_malformed_response", retryable=False) from error
        if decision.answer is not None and len(decision.answer) > self._max_answer_characters:
            raise LLMProviderError("llm_answer_too_large", retryable=False)
        return decision

    def _structured_chat_payload(
        self,
        *,
        instructions: str,
        user_content: str,
        max_tokens: int,
        schema_name: str,
        schema: dict[str, object],
    ) -> dict[str, object]:
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        }

    @staticmethod
    def _agent_decision_schema() -> dict[str, object]:
        schema = AgentDecision.model_json_schema()
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            raise LLMProviderError("llm_schema_invalid", retryable=False)
        schema["required"] = list(properties)
        return schema

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request_with_retry(self, payload: dict[str, object]) -> httpx.Response:
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.post(
                    "/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                        "X-Client-Request-Id": get_request_id(),
                    },
                    content=json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
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

    @staticmethod
    def _validated_output_text(response: httpx.Response) -> str:
        try:
            parsed = _ChatCompletionsPayload.model_validate(response.json())
        except (ValueError, ValidationError) as error:
            raise LLMProviderError("llm_malformed_response", retryable=False) from error
        if len(parsed.choices) != 1:
            raise LLMProviderError("llm_unusable_response", retryable=False)
        choice = parsed.choices[0]
        if choice.finish_reason in {"length", "content_filter"}:
            raise LLMProviderError("llm_unusable_response", retryable=False)
        if choice.message.refusal is not None or choice.message.content is None:
            raise LLMProviderError("llm_unusable_response", retryable=False)
        output_text = choice.message.content.strip()
        if output_text.startswith("```") and output_text.endswith("```"):
            first_newline = output_text.find("\n")
            if first_newline != -1:
                output_text = output_text[first_newline + 1 : -3].strip()
        if not output_text:
            raise LLMProviderError("llm_unusable_response", retryable=False)
        return output_text

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

    async def decide(self, request: AgentGenerationRequest) -> AgentDecision:
        payload = json.loads(request.context_payload)
        question = payload.get("question")
        evidence = payload.get("evidence")
        completed = payload.get("completed_tools")
        failed = payload.get("failed_tools")
        failure_codes = payload.get("failed_tool_codes")
        if not isinstance(question, str) or not isinstance(evidence, list):
            raise LLMProviderError("llm_malformed_input", retryable=False)
        completed_names = set(completed) if isinstance(completed, list) else set()
        failed_names = set(failed) if isinstance(failed, list) else set()
        attempted_names = completed_names | failed_names
        failed_code_names = set(failure_codes) if isinstance(failure_codes, list) else set()
        wants_callers = bool(
            re.search(
                r"\b(?:callers?|calls|called by|depends on|impact|breaks? if|"
                r"instantiat(?:e|es|ed))\b",
                question,
                re.I,
            )
        )
        wants_history = bool(
            re.search(r"\b(?:history|commit|pull request|changed|introduced)\b", question, re.I)
        )
        wants_code = (not wants_history and not wants_callers) or bool(
            re.search(r"\b(?:code|implementation|function|class|method)\b", question, re.I)
        )
        if wants_callers and AgentToolName.FIND_CALLERS.value not in attempted_names:
            symbol = self._caller_symbol(question)
            if symbol is not None:
                return AgentDecision(
                    action="tool",
                    tool_name=AgentToolName.FIND_CALLERS,
                    arguments=AgentToolArguments(symbol_name=symbol),
                )
        if wants_code and AgentToolName.SEARCH_CODE.value not in attempted_names:
            return AgentDecision(
                action="tool",
                tool_name=AgentToolName.SEARCH_CODE,
                arguments=AgentToolArguments(query=question),
            )
        if wants_history and AgentToolName.GET_HISTORY.value not in attempted_names:
            return AgentDecision(
                action="tool",
                tool_name=AgentToolName.GET_HISTORY,
                arguments=AgentToolArguments(query=question),
            )
        ids = [item.get("id") for item in evidence if isinstance(item, dict)]
        valid_ids = [item for item in ids if isinstance(item, str)]
        if not valid_ids:
            return AgentDecision(
                action="final",
                answer=(
                    "Repository analysis is temporarily unavailable."
                    if failed_code_names
                    and not failed_code_names.issubset({"caller_target_ambiguous"})
                    else "The available evidence is insufficient to answer this question."
                ),
                answerability=(
                    Answerability.TEMPORARILY_UNAVAILABLE
                    if failed_code_names
                    and not failed_code_names.issubset({"caller_target_ambiguous"})
                    else Answerability.INSUFFICIENT_EVIDENCE
                ),
                uncertainty=(
                    AnswerUncertainty.NOT_APPLICABLE
                    if failed_code_names
                    and not failed_code_names.issubset({"caller_target_ambiguous"})
                    else AnswerUncertainty.HIGH
                ),
                evidence_ids=[],
            )
        return AgentDecision(
            action="final",
            answer=(
                "The authorized repository evidence contains relevant implementation "
                "or history records."
            ),
            answerability=(
                Answerability.PARTIALLY_ANSWERED if failed_names else Answerability.ANSWERED
            ),
            uncertainty=AnswerUncertainty.MEDIUM,
            evidence_ids=valid_ids[:2],
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
                    "target_symbol_name",
                    "target_qualified_name",
                    "caller_symbol_name",
                    "caller_qualified_name",
                    "call_expression",
                )
            )
        searchable = "\n".join(searchable_parts).casefold()
        if {"prompt", "injection"}.issubset(question_tokens) and "ignore all prior" in searchable:
            return True
        return any(token in searchable for token in question_tokens)

    @staticmethod
    def _caller_symbol(question: str) -> str | None:
        quoted = re.search(r"`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)`", question)
        if quoted is not None:
            return quoted.group(1)
        patterns = (
            r"(?:callers?\s+(?:of|for)|calls?|called\s+by|depends\s+on|instantiates?)\s+"
            r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)",
            r"(?:breaks?|impact)\s+if\s+([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)",
        )
        for pattern in patterns:
            match = re.search(pattern, question, re.I)
            if match is not None:
                return match.group(1)
        return None


def create_llm_provider(settings: Settings) -> LLMProviderProtocol:
    """Build the configured provider behind one application-owned protocol."""
    if settings.llm_provider is LLMProvider.DETERMINISTIC:
        return DeterministicLLMProvider()

    if str(settings.llm_api_url).rstrip("/") == GEMINI_API_BASE_URL:
        return GeminiChatCompletionsClient(settings)

    return OpenAIResponsesClient(settings)
