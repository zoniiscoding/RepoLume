"""Provider compatibility boundary for the direct agent loop."""

import json
from typing import cast

from app.agent.models import (
    AgentDecision,
    AgentGenerationRequest,
    AgentProviderProtocol,
    AgentToolName,
)
from app.llm.client import GroundedGenerationRequest, LLMProviderProtocol
from app.rag.models import Answerability, AnswerUncertainty


class LegacyGenerationAgentAdapter:
    """Keep injected Milestone 6 synthesis fakes usable while production uses `decide`."""

    def __init__(self, provider: LLMProviderProtocol) -> None:
        self._provider = provider

    async def decide(self, request: AgentGenerationRequest) -> AgentDecision:
        payload = json.loads(request.context_payload)
        evidence = payload.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            question = payload.get("question")
            if not isinstance(question, str):
                question = "repository question"
            return AgentDecision(
                action="tool",
                tool_name=AgentToolName.SEARCH_CODE,
                arguments={"query": question},
            )
        draft = await self._provider.generate(
            GroundedGenerationRequest(
                instructions=request.instructions,
                evidence_payload=request.context_payload,
            )
        )
        available_ids = [
            item.get("id") for item in evidence if isinstance(item, dict) and item.get("id")
        ]
        resolved_ids = [
            available_ids[int(item[1:]) - 1]
            if item.startswith("E")
            and item[1:].isdigit()
            and 0 < int(item[1:]) <= len(available_ids)
            else item
            for item in draft.evidence_ids
        ]
        return AgentDecision(
            action="final",
            answer=draft.answer,
            answerability=Answerability(draft.answerability.value),
            uncertainty=AnswerUncertainty(draft.uncertainty.value),
            evidence_ids=resolved_ids,
        )


def resolve_agent_provider(provider: LLMProviderProtocol) -> AgentProviderProtocol:
    candidate = getattr(provider, "decide", None)
    if callable(candidate):
        return cast(AgentProviderProtocol, provider)
    return LegacyGenerationAgentAdapter(provider)
