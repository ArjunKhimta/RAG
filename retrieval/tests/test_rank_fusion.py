from __future__ import annotations

import pytest

from retrieval.config import MAX_SEARCH_LIMIT
from retrieval.rank_fusion import reciprocal_rank_fusion
from retrieval.search_results import SearchResult


def _result(chunk_id: str, score: float = 0.5) -> SearchResult:
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
        score=score,
    )


def _results(*chunk_ids: str) -> list[SearchResult]:
    return [_result(chunk_id) for chunk_id in chunk_ids]


def test_a_chunk_in_both_lists_earns_one_over_k_plus_rank_from_each():
    fused = reciprocal_rank_fusion(
        {"vector": _results("a", "b", "shared"), "keyword": _results("shared")}, limit=10
    )

    shared = next(result for result in fused if result.result.chunk_id == "shared")
    assert shared.fused_score == pytest.approx(1 / 61 + 1 / 63)
    assert shared.ranks == {"vector": 3, "keyword": 1}


def test_a_chunk_in_one_list_earns_once_and_has_no_rank_in_the_other():
    fused = reciprocal_rank_fusion(
        {"vector": _results("only_vector"), "keyword": _results("only_keyword")}, limit=10
    )

    only_vector = next(result for result in fused if result.result.chunk_id == "only_vector")
    assert only_vector.fused_score == pytest.approx(1 / 61)
    assert only_vector.ranks == {"vector": 1}
    assert "keyword" not in only_vector.ranks


def test_agreement_between_lists_beats_a_single_top_rank():
    fused = reciprocal_rank_fusion(
        {
            "vector": _results("vector_favourite", "a", "b", "agreed"),
            "keyword": _results("keyword_favourite", "c", "agreed"),
        },
        limit=10,
    )

    assert fused[0].result.chunk_id == "agreed"


def test_equal_fused_scores_are_ordered_by_best_rank_then_chunk_id():
    fused = reciprocal_rank_fusion(
        {"vector": _results("zeta", "beta"), "keyword": _results("alpha", "gamma")}, limit=10
    )

    assert [result.result.chunk_id for result in fused] == ["alpha", "zeta", "beta", "gamma"]


def test_the_same_input_always_gives_the_same_order():
    ranked_lists = {"vector": _results("c", "a", "b"), "keyword": _results("b", "c", "d")}

    first_order = [result.result.chunk_id for result in reciprocal_rank_fusion(ranked_lists, 10)]
    second_order = [result.result.chunk_id for result in reciprocal_rank_fusion(ranked_lists, 10)]

    assert first_order == second_order


def test_only_the_best_limit_results_are_kept():
    fused = reciprocal_rank_fusion(
        {"vector": _results("a", "b", "c"), "keyword": _results("d", "e")}, limit=2
    )

    assert len(fused) == 2


def test_each_lists_original_score_is_kept():
    fused = reciprocal_rank_fusion(
        {"vector": [_result("shared", score=0.91)], "keyword": [_result("shared", score=14.2)]},
        limit=10,
    )

    assert fused[0].scores == {"vector": 0.91, "keyword": 14.2}


def test_a_chunk_repeated_within_one_list_counts_once_at_its_best_rank():
    fused = reciprocal_rank_fusion({"vector": _results("a", "b", "a")}, limit=10)

    first = next(result for result in fused if result.result.chunk_id == "a")
    assert first.ranks == {"vector": 1}
    assert first.fused_score == pytest.approx(1 / 61)


def test_empty_lists_fuse_to_nothing_and_one_empty_list_is_ignored():
    assert reciprocal_rank_fusion({"vector": [], "keyword": []}, limit=10) == []

    fused = reciprocal_rank_fusion({"vector": _results("a"), "keyword": []}, limit=10)

    assert [result.result.chunk_id for result in fused] == ["a"]


def test_a_smaller_k_lets_top_ranks_dominate():
    ranked_lists = {
        "vector": _results("top", "a", "b", "c", "agreed"),
        "keyword": _results("x", "y", "z", "w", "agreed"),
    }

    default_order = reciprocal_rank_fusion(ranked_lists, limit=10)
    steep_order = reciprocal_rank_fusion(ranked_lists, limit=10, k=1)

    assert default_order[0].result.chunk_id == "agreed"
    assert steep_order[0].result.chunk_id != "agreed"


@pytest.mark.parametrize("k", [0, -5])
def test_k_must_be_positive(k):
    with pytest.raises(ValueError):
        reciprocal_rank_fusion({"vector": _results("a")}, limit=10, k=k)


@pytest.mark.parametrize("limit", [0, MAX_SEARCH_LIMIT + 1])
def test_a_limit_outside_the_allowed_range_is_rejected(limit):
    with pytest.raises(ValueError):
        reciprocal_rank_fusion({"vector": _results("a")}, limit=limit)
