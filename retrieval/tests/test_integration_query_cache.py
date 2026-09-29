"""Cache a real Gemini question embedding in real MongoDB Atlas. Excluded from the default run.

    pytest retrieval/tests -m integration

Writes only to a throwaway `code_search_query_cache_test_<random>` database, which is dropped
afterwards. Costs one embedding request.
"""

from __future__ import annotations

import math
import os
import uuid
from collections.abc import Iterator

import pytest

from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import (
    EMBEDDING_DIMENSIONS,
    GEMINI_API_KEY_VARIABLE,
    MONGODB_URI_VARIABLE,
    load_environment,
)
from retrieval.embedders import GeminiQueryEmbedder
from retrieval.query_cache import (
    QUERY_EMBEDDINGS_COLLECTION,
    CachingQueryEmbedder,
    MongoQueryEmbeddingStore,
)

pytestmark = pytest.mark.integration

QUESTION = "How are URL rules registered on an application?"


@pytest.fixture(scope="module", autouse=True)
def configured_environment() -> None:
    load_environment()
    for variable_name in (GEMINI_API_KEY_VARIABLE, MONGODB_URI_VARIABLE):
        if not os.environ.get(variable_name):
            pytest.skip(f"{variable_name} is not configured")


@pytest.fixture
def test_database() -> Iterator:
    mongo_client = build_mongo_client()
    database_name = f"code_search_query_cache_test_{uuid.uuid4().hex[:8]}"
    try:
        yield mongo_client[database_name]
    finally:
        mongo_client.drop_database(database_name)
        mongo_client.close()


def test_asking_the_same_question_twice_embeds_it_once(test_database):
    store = MongoQueryEmbeddingStore(test_database)
    store.ensure_indexes()
    caching_embedder = CachingQueryEmbedder(GeminiQueryEmbedder(build_gemini_client()), store)

    first_vector = caching_embedder.embed_query(QUESTION)
    second_vector = caching_embedder.embed_query(f"  {QUESTION}  ")

    stored_document = test_database[QUERY_EMBEDDINGS_COLLECTION].find_one()
    assert (caching_embedder.miss_count, caching_embedder.hit_count) == (1, 1)
    assert second_vector == first_vector
    assert len(first_vector) == EMBEDDING_DIMENSIONS
    assert math.isclose(math.hypot(*first_vector), 1.0, rel_tol=1e-6)
    assert QUESTION not in str(stored_document)
    assert test_database[QUERY_EMBEDDINGS_COLLECTION].count_documents({}) == 1
