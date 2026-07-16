"""Deterministic evidence selection and independent citation resolution."""

import re
from collections import defaultdict
from collections.abc import Sequence

from app.core.config import Settings
from app.llm.client import DraftAnswerability, GroundedAnswerDraft
from app.rag.models import Answerability, Citation, Evidence
from app.vector.qdrant import RetrievalHit

_INLINE_EVIDENCE_PATTERN = re.compile(r"\[(E\d+)\]")


class EvidenceSelector:
    def __init__(self, settings: Settings) -> None:
        self._limit = settings.rag_retrieval_top_k
        self._max_per_file = settings.rag_max_evidence_per_file
        self._max_total_bytes = settings.rag_max_evidence_bytes
        self._max_item_bytes = settings.rag_max_evidence_item_bytes

    def select(self, hits: Sequence[RetrievalHit]) -> tuple[Evidence, ...]:
        ordered = sorted(
            hits,
            key=lambda hit: (
                -hit.score,
                hit.file_path,
                hit.start_line,
                hit.end_line,
                hit.stable_chunk_hash,
            ),
        )
        selected: list[RetrievalHit] = []
        seen_hashes: set[str] = set()
        per_file: defaultdict[str, int] = defaultdict(int)
        total_bytes = 0
        for hit in ordered:
            item_bytes = len(hit.content.encode("utf-8"))
            if (
                hit.stable_chunk_hash in seen_hashes
                or item_bytes > self._max_item_bytes
                or per_file[hit.file_path] >= self._max_per_file
                or total_bytes + item_bytes > self._max_total_bytes
                or self._overlaps_selected(hit, selected)
            ):
                continue
            selected.append(hit)
            seen_hashes.add(hit.stable_chunk_hash)
            per_file[hit.file_path] += 1
            total_bytes += item_bytes
            if len(selected) == self._limit:
                break
        return tuple(
            Evidence(
                evidence_id=f"E{index}",
                score=hit.score,
                file_path=hit.file_path,
                language=hit.language,
                chunk_type=hit.chunk_type,
                symbol_name=hit.symbol_name,
                qualified_symbol_name=hit.qualified_symbol_name,
                start_line=hit.start_line,
                end_line=hit.end_line,
                stable_chunk_hash=hit.stable_chunk_hash,
                content=hit.content,
            )
            for index, hit in enumerate(selected, start=1)
        )

    @staticmethod
    def _overlaps_selected(candidate: RetrievalHit, selected: Sequence[RetrievalHit]) -> bool:
        return any(
            candidate.file_path == item.file_path
            and candidate.start_line <= item.end_line
            and item.start_line <= candidate.end_line
            for item in selected
        )


class CitationValidator:
    """Resolve model IDs against immutable server evidence; never accept model metadata."""

    def validate(
        self,
        draft: GroundedAnswerDraft,
        evidence: Sequence[Evidence],
        *,
        commit_sha: str,
    ) -> tuple[Answerability, tuple[Citation, ...]]:
        by_id = {item.evidence_id: item for item in evidence}
        unique_ids = tuple(dict.fromkeys(draft.evidence_ids))
        inline_ids = set(_INLINE_EVIDENCE_PATTERN.findall(draft.answer))
        if (
            any(item_id not in by_id for item_id in unique_ids)
            or not inline_ids.issubset(by_id)
            or not inline_ids.issubset(unique_ids)
        ):
            return Answerability.INSUFFICIENT_EVIDENCE, ()
        if draft.answerability is DraftAnswerability.ANSWERED and not unique_ids:
            return Answerability.INSUFFICIENT_EVIDENCE, ()
        if draft.answerability is not DraftAnswerability.ANSWERED:
            return Answerability(draft.answerability.value), ()
        citations = tuple(
            Citation(
                evidence_id=item.evidence_id,
                file_path=item.file_path,
                start_line=item.start_line,
                end_line=item.end_line,
                symbol_name=item.symbol_name,
                qualified_symbol_name=item.qualified_symbol_name,
                chunk_type=item.chunk_type,
                commit_sha=commit_sha,
                supporting_excerpt=item.content,
            )
            for item_id in unique_ids
            for item in (by_id[item_id],)
        )
        return Answerability.ANSWERED, citations
