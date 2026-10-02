from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from retrieval.embedding_inputs import build_result_input
from retrieval.reranking import PairScores, rerank
from retrieval.search_results import SearchResult


def _result(chunk_id: str) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        file_path="pkg/module.py",
        start_line=1,
        end_line=2,
        kind="function",
        qualified_name=chunk_id,
        signature=None,
        part_number=1,
        part_count=1,
        is_test_file=False,
        text=f"def {chunk_id}():\n    pass",
        score=0.5,
    )


@dataclass
class FakeScorer:
    scores: list[float]
    truncated: list[bool] | None = None
    received_questions: list[str] = field(default_factory=list)
    received_passages: list[list[str]] = field(default_factory=list)

    def score_pairs(self, question: str, passages: list[str]) -> PairScores:
        self.received_questions.append(question)
        self.received_passages.append(passages)
        truncated = self.truncated if self.truncated is not None else [False] * len(self.scores)
        return PairScores(scores=self.scores, truncated=truncated)


def test_candidates_are_reordered_by_reranker_score_best_first():
    candidates = [_result("first"), _result("second"), _result("third")]
    scorer = FakeScorer(scores=[-2.0, 5.0, 1.0])

    reranked = rerank("question", candidates, scorer, limit=3)

    assert [item.result.chunk_id for item in reranked] == ["second", "third", "first"]
    assert [item.rerank_score for item in reranked] == [5.0, 1.0, -2.0]


def test_each_result_keeps_its_rank_before_reranking():
    candidates = [_result("first"), _result("second"), _result("third")]
    scorer = FakeScorer(scores=[-2.0, 5.0, 1.0])

    reranked = rerank("question", candidates, scorer, limit=3)

    assert [item.original_rank for item in reranked] == [2, 3, 1]


def test_only_the_best_limit_results_are_kept():
    candidates = [_result(f"chunk{index}") for index in range(6)]
    scorer = FakeScorer(scores=[0.0, 6.0, 1.0, 5.0, 2.0, 4.0])

    reranked = rerank("question", candidates, scorer, limit=2)

    assert [item.result.chunk_id for item in reranked] == ["chunk1", "chunk3"]


def test_equal_scores_keep_the_original_order():
    candidates = [_result("first"), _result("second"), _result("third")]
    scorer = FakeScorer(scores=[1.0, 1.0, 1.0])

    reranked = rerank("question", candidates, scorer, limit=3)

    assert [item.result.chunk_id for item in reranked] == ["first", "second", "third"]


def test_truncation_flags_stay_with_their_candidates():
    candidates = [_result("short"), _result("long")]
    scorer = FakeScorer(scores=[1.0, 2.0], truncated=[False, True])

    reranked = rerank("question", candidates, scorer, limit=2)

    assert [(item.result.chunk_id, item.was_truncated) for item in reranked] == [
        ("long", True),
        ("short", False),
    ]


def test_the_scorer_sees_the_question_and_the_same_text_the_embedder_saw():
    candidates = [_result("first"), _result("second")]
    scorer = FakeScorer(scores=[1.0, 2.0])

    rerank("How are routes added?", candidates, scorer, limit=2)

    assert scorer.received_questions == ["How are routes added?"]
    assert scorer.received_passages == [[build_result_input(item) for item in candidates]]


def test_no_candidates_means_no_results_and_no_scoring():
    scorer = FakeScorer(scores=[])

    assert rerank("question", [], scorer, limit=5) == []
    assert scorer.received_questions == []


def test_a_scorer_returning_the_wrong_number_of_scores_is_rejected():
    candidates = [_result("first"), _result("second")]
    scorer = FakeScorer(scores=[1.0])

    with pytest.raises(ValueError, match="one score and one truncation flag per candidate"):
        rerank("question", candidates, scorer, limit=2)


def test_a_limit_outside_the_allowed_range_is_rejected():
    with pytest.raises(ValueError, match="between 1 and"):
        rerank("question", [_result("first")], FakeScorer(scores=[1.0]), limit=0)
