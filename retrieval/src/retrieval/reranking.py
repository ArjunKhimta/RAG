"""Reranking: read the question and each candidate together, then keep the best few.

Vector search compares two vectors computed separately, the chunk's long before the question was
asked, so the chunk's vector has to summarize everything it might be asked about. A cross-encoder
reads the question and one chunk as a single input and outputs one relevance score, so every word
of the question can be weighed against every token of the code. That is more accurate, but it
must run once per pair at question time, so it reorders only the top `RERANK_CANDIDATE_COUNT` (30)
candidates of a cheaper search and keeps the best `RERANK_RESULT_COUNT` (5). It can only reorder
what it is given: a chunk outside the candidates stays lost.

Each candidate is shown as the same text the embedder saw: file path, definition name, then code.
Scores are the model's raw outputs: higher means more relevant, and they compare candidates for one
question, not across questions. Equal scores keep the candidates' original order, so the same input
always gives the same output.

This module holds what ranking needs without the model itself. The model runs in
`retrieval.cross_encoder`, which alone imports ONNX Runtime, tokenizers, and numpy, so a process
that never reranks (the retrieval service) never loads them and its image leaves them out.

The model only produces numbers. Text planted in a chunk can at most move that chunk's score; it
cannot make the model do anything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from retrieval.config import RERANK_RESULT_COUNT
from retrieval.embedding_inputs import build_result_input
from retrieval.search_results import SearchResult, validate_search_limit


class QuestionTooLongError(ValueError):
    """Raised when a question would leave less than half the model's window for the code."""


@dataclass(frozen=True)
class PairScores:
    scores: list[float]
    truncated: list[bool]


class PairScorer(Protocol):
    def score_pairs(self, question: str, passages: list[str]) -> PairScores: ...


@dataclass(frozen=True)
class RerankedResult:
    result: SearchResult
    rerank_score: float
    original_rank: int
    was_truncated: bool


def rerank(
    question: str,
    candidates: list[SearchResult],
    scorer: PairScorer,
    limit: int = RERANK_RESULT_COUNT,
) -> list[RerankedResult]:
    """Score every candidate against the question and return the best `limit`, best first."""
    validate_search_limit(limit)
    if not candidates:
        return []
    passages = [build_result_input(candidate) for candidate in candidates]
    pair_scores = scorer.score_pairs(question, passages)
    if len(pair_scores.scores) != len(candidates) or len(pair_scores.truncated) != len(candidates):
        raise ValueError("The scorer must return one score and one truncation flag per candidate")
    reranked_results = [
        RerankedResult(
            result=candidate,
            rerank_score=score,
            original_rank=original_rank,
            was_truncated=was_truncated,
        )
        for original_rank, (candidate, score, was_truncated) in enumerate(
            zip(candidates, pair_scores.scores, pair_scores.truncated, strict=True), start=1
        )
    ]
    reranked_results.sort(key=_reranked_order)
    return reranked_results[:limit]


def _reranked_order(reranked_result: RerankedResult) -> tuple[float, int]:
    return (-reranked_result.rerank_score, reranked_result.original_rank)
