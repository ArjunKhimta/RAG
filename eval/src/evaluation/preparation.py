"""The checks and connections every evaluation run needs before its first question.

A run is refused when the question file was written for a different commit than the one indexed,
when the version was indexed before the call graph, or when a search index is not ready, so
results are never produced from a mismatched or half-built index.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google import genai
from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore
from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import KEYWORD_INDEX_NAME, MONGODB_DATABASE, VECTOR_INDEX_NAME
from retrieval.embedders import GeminiQueryEmbedder
from retrieval.graph_expansion import require_call_graph
from retrieval.query_cache import CachingQueryEmbedder, MongoQueryEmbeddingStore
from retrieval.question_set import QuestionSet, load_question_set
from retrieval.reranker_model import verified_reranker_files
from retrieval.reranking import CrossEncoderScorer
from retrieval.search_indexes import require_queryable_index
from retrieval.search_results import SearchRefusedError, require_indexed_version
from retrieval.vector_search import require_searchable_version

from evaluation.search_setups import SearchContext


@dataclass(frozen=True)
class PreparedRun:
    question_set: QuestionSet
    repository_record: dict[str, Any]
    context: SearchContext
    embedder: CachingQueryEmbedder
    gemini_client: genai.Client


def prepare_run(question_path: Path) -> PreparedRun:
    question_set = load_question_set(question_path)
    database = build_mongo_client()[MONGODB_DATABASE]
    repository = question_set.repository
    version = question_set.version
    record = MongoChunkStore(database).find_repository_record(repository, version)
    require_indexed_version(record, repository, version)
    require_call_graph(record, repository, version)
    if record["commit_id"] != question_set.commit_id:
        raise SearchRefusedError(
            f"The questions were written for commit {question_set.commit_id}, but the index is at "
            f"{record['commit_id']}"
        )
    query_embedding_store = MongoQueryEmbeddingStore(database)
    query_embedding_store.ensure_indexes()
    gemini_client = build_gemini_client()
    embedder = CachingQueryEmbedder(GeminiQueryEmbedder(gemini_client), query_embedding_store)
    require_searchable_version(record, repository, version, embedder)
    chunks_collection = database[CHUNKS_COLLECTION]
    require_queryable_index(chunks_collection, VECTOR_INDEX_NAME)
    require_queryable_index(chunks_collection, KEYWORD_INDEX_NAME)
    context = SearchContext(
        chunks_collection=chunks_collection,
        embedder=embedder,
        scorer=CrossEncoderScorer(verified_reranker_files()),
        repository=repository,
        version=version,
    )
    return PreparedRun(
        question_set=question_set,
        repository_record=record,
        context=context,
        embedder=embedder,
        gemini_client=gemini_client,
    )
