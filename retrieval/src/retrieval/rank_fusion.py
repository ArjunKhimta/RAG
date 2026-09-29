"""Reciprocal Rank Fusion: combine ranked result lists by position, never by score.

Vector scores sit between 0 and 1 while BM25 scores are unbounded and change from query to query,
so adding them would let one search dominate some queries and vanish from others. Rank positions
mean the same thing in every list.

Each chunk earns 1 / (k + rank) from every list it appears in, with ranks starting at 1, and the
earnings are summed. With k = 60, rank 1 is worth 1/61 and rank 10 is worth 1/70, only 13% less,
so appearing in both lists matters far more than the exact position within one: ranks 1 and 3
give 1/61 + 1/63 = 0.0323, while rank 1 in a single list gives 0.0164. A chunk both searches
consider relevant beats a chunk only one of them loves.

k = 60 comes from Cormack, Clarke, and Buettcher (2009) and is the default in Elasticsearch,
OpenSearch, and MongoDB's `$rankFusion`. It is not yet tuned for code.

Equal fused scores are ordered by the best single rank, then by chunk ID, so the same input always
gives the same order.
"""

from __future__ import annotations

from dataclasses import dataclass

from retrieval.config import RRF_K
from retrieval.search_results import SearchResult, validate_search_limit


@dataclass(frozen=True)
class FusedResult:
    """A chunk's fused score, and its rank and original score in each list that contained it.

    `result` holds the chunk's fields; its `score` is from whichever list found it first, so use
    `scores` for per-list scores.
    """

    result: SearchResult
    fused_score: float
    ranks: dict[str, int]
    scores: dict[str, float]

    @property
    def best_rank(self) -> int:
        return min(self.ranks.values())


def reciprocal_rank_fusion(
    ranked_lists: dict[str, list[SearchResult]], limit: int, k: int = RRF_K
) -> list[FusedResult]:
    """Fuse named lists, each ordered best first, and return the best `limit` chunks."""
    validate_search_limit(limit)
    if k <= 0:
        raise ValueError("The RRF constant k must be positive")
    results_by_chunk: dict[str, SearchResult] = {}
    ranks_by_chunk: dict[str, dict[str, int]] = {}
    scores_by_chunk: dict[str, dict[str, float]] = {}
    for list_name, results in ranked_lists.items():
        for rank, result in enumerate(results, start=1):
            chunk_ranks = ranks_by_chunk.setdefault(result.chunk_id, {})
            if list_name in chunk_ranks:
                continue
            chunk_ranks[list_name] = rank
            scores_by_chunk.setdefault(result.chunk_id, {})[list_name] = result.score
            results_by_chunk.setdefault(result.chunk_id, result)
    fused_results = [
        FusedResult(
            result=results_by_chunk[chunk_id],
            fused_score=sum(1 / (k + rank) for rank in chunk_ranks.values()),
            ranks=chunk_ranks,
            scores=scores_by_chunk[chunk_id],
        )
        for chunk_id, chunk_ranks in ranks_by_chunk.items()
    ]
    fused_results.sort(key=_fused_order)
    return fused_results[:limit]


def _fused_order(fused_result: FusedResult) -> tuple[float, int, str]:
    return (-fused_result.fused_score, fused_result.best_rank, fused_result.result.chunk_id)
