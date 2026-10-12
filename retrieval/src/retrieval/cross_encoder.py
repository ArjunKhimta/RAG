"""The cross-encoder that scores question-and-chunk pairs for reranking (see `retrieval.reranking`).

The model is `cross-encoder/ms-marco-MiniLM-L6-v2` (22.7M parameters, Apache-2.0), run on the CPU
with ONNX Runtime, which is far smaller than PyTorch. It was trained on web search (MS MARCO), not
code, so whether it helps here is for the evaluation to show.

The model reads at most `RERANKER_MAX_TOKENS` (512) tokens per pair. Only the chunk is cut, from
its end, so the path, name, and opening lines always fit, and each result records whether its
chunk was cut. A question longer than half that window is refused rather than leaving almost no
room for the code.

Candidates are scored one at a time (`RERANKER_BATCH_SIZE` = 1). On the CPU, batching was no
faster, but memory grew with batch size: with 30 inputs of the full 512 tokens, peak memory was
284 MB one at a time and 775 MB in batches of 8, and the server will have 512 MB.

This is the only module that imports ONNX Runtime, tokenizers, and numpy. The retrieval service's
image leaves those packages out (they are in `requirements-reranker.txt`), so nothing the service
loads may import this module; `retrieval.asking` imports it only when a reranker is first built.
"""

from __future__ import annotations

import numpy
import onnxruntime
from tokenizers import Encoding, Tokenizer

from retrieval.config import RERANKER_BATCH_SIZE, RERANKER_MAX_TOKENS
from retrieval.reranker_model import RerankerFiles
from retrieval.reranking import PairScores, QuestionTooLongError

CPU_PROVIDER = "CPUExecutionProvider"

TRUNCATE_CODE_ONLY = "only_second"


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
