"""Reranking: read the question and each candidate together, then keep the best few.

Vector search compares two vectors computed separately, the chunk's long before the question was
asked, so the chunk's vector has to summarize everything it might be asked about. A cross-encoder
reads the question and one chunk as a single input and outputs one relevance score, so every word
of the question can be weighed against every token of the code. That is more accurate, but it
must run once per pair at question time, so it reorders only the top `RERANK_CANDIDATE_COUNT` (30)
candidates of a cheaper search and keeps the best `RERANK_RESULT_COUNT` (5). It can only reorder
what it is given: a chunk outside the candidates stays lost.

The model is `cross-encoder/ms-marco-MiniLM-L6-v2` (22.7M parameters, Apache-2.0), run on the CPU
with ONNX Runtime, which is far smaller than PyTorch. It was trained on web search (MS MARCO), not
code, so whether it helps here is for the evaluation to show.

Each candidate is shown as the same text the embedder saw: file path, definition name, then code.
The model reads at most `RERANKER_MAX_TOKENS` (512) tokens per pair. Only the chunk is cut, from
its end, so the path, name, and opening lines always fit, and each result records whether its
chunk was cut. A question longer than half that window is refused rather than leaving almost no
room for the code.

Scores are the model's raw outputs: higher means more relevant, and they compare candidates for one
question, not across questions. Candidates are scored one at a time (`RERANKER_BATCH_SIZE` = 1).
On the CPU, batching was no faster, but memory grew with batch size: with 30 inputs of the full 512
tokens, peak memory was 284 MB one at a time and 775 MB in batches of 8, and the server will have
512 MB. Equal scores keep the candidates' original order, so the same input always gives the same
output.

The model only produces numbers. Text planted in a chunk can at most move that chunk's score; it
cannot make the model do anything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy
import onnxruntime
from tokenizers import Encoding, Tokenizer

from retrieval.config import RERANK_RESULT_COUNT, RERANKER_BATCH_SIZE, RERANKER_MAX_TOKENS
from retrieval.embedding_inputs import build_result_input
from retrieval.reranker_model import RerankerFiles
from retrieval.search_results import SearchResult, validate_search_limit

CPU_PROVIDER = "CPUExecutionProvider"

TRUNCATE_CODE_ONLY = "only_second"


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


class CrossEncoderScorer:
    """Scores question-and-passage pairs with the ONNX cross-encoder on the CPU.

    Load it once and reuse it: reading the model takes far longer than scoring 30 pairs.
    """

    def __init__(
        self,
        files: RerankerFiles,
        max_tokens: int = RERANKER_MAX_TOKENS,
        batch_size: int = RERANKER_BATCH_SIZE,
    ) -> None:
        if batch_size < 1:
            raise ValueError("The batch size must be at least 1")
        self._max_tokens = max_tokens
        self._batch_size = batch_size
        self._question_tokenizer = Tokenizer.from_file(str(files.tokenizer_path))
        self._pair_tokenizer = Tokenizer.from_file(str(files.tokenizer_path))
        self._pair_tokenizer.enable_truncation(max_length=max_tokens, strategy=TRUNCATE_CODE_ONLY)
        self._pair_tokenizer.enable_padding()
        self._session = onnxruntime.InferenceSession(
            str(files.model_path), providers=[CPU_PROVIDER]
        )

    def score_pairs(self, question: str, passages: list[str]) -> PairScores:
        self._require_question_fits(question)
        scores: list[float] = []
        truncated: list[bool] = []
        for batch_start in range(0, len(passages), self._batch_size):
            batch_passages = passages[batch_start : batch_start + self._batch_size]
            encodings = self._pair_tokenizer.encode_batch(
                [(question, passage) for passage in batch_passages]
            )
            logits = self._session.run(None, _model_inputs(encodings))[0]
            scores.extend(float(row[0]) for row in logits)
            truncated.extend(bool(encoding.overflowing) for encoding in encodings)
        return PairScores(scores=scores, truncated=truncated)

    def _require_question_fits(self, question: str) -> None:
        question_token_count = len(self._question_tokenizer.encode(question).ids)
        if question_token_count > self._max_tokens // 2:
            raise QuestionTooLongError(
                f"The question is {question_token_count} tokens; the reranker accepts at most "
                f"{self._max_tokens // 2}, half its {self._max_tokens}-token window"
            )


def _model_inputs(encodings: list[Encoding]) -> dict[str, numpy.ndarray]:
    return {
        "input_ids": numpy.array([encoding.ids for encoding in encodings], dtype=numpy.int64),
        "attention_mask": numpy.array(
            [encoding.attention_mask for encoding in encodings], dtype=numpy.int64
        ),
        "token_type_ids": numpy.array(
            [encoding.type_ids for encoding in encodings], dtype=numpy.int64
        ),
    }


def _reranked_order(reranked_result: RerankedResult) -> tuple[float, int]:
    return (-reranked_result.rerank_score, reranked_result.original_rank)
