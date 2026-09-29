"""Storage for embedded chunks and indexed repository versions in MongoDB.

`chunks` holds one document per chunk: every chunk field, the repository and version, the
embedding cache key, and the vector. The collection doubles as the embedding cache: before calling
the model, the indexer looks up existing documents by `embedding_key` and reuses their vectors,
so no second copy of each vector is stored.

Vectors are stored as BSON binary float32, 4 bytes per dimension. A plain BSON array would store
each element with a text key and an 8-byte double, about 13 bytes per dimension. Atlas Vector
Search indexes the binary form directly.

Chunk IDs are readable and deterministic, such as
`pallets/flask@3.1.3:src/flask/app.py:546:method:1`, so indexing a version again overwrites
documents in place instead of adding duplicates.

`repositories` holds one document per indexed version, including the license that answers show.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from bson.binary import Binary, BinaryVectorDtype
from pymongo import ASCENDING, ReplaceOne
from pymongo.database import Database

from retrieval.chunker import CodeChunk
from retrieval.repository_cloner import CloneMetadata

CHUNKS_COLLECTION = "chunks"

REPOSITORIES_COLLECTION = "repositories"

CACHE_LOOKUP_BATCH_SIZE = 1000


@dataclass(frozen=True)
class CachedEmbedding:
    vector: list[float]
    is_truncated: bool


@dataclass(frozen=True)
class StoredChunk:
    chunk_id: str
    metadata: CloneMetadata
    chunk: CodeChunk
    embedding_key: str
    embedding: CachedEmbedding


@dataclass(frozen=True)
class IndexedVersion:
    metadata: CloneMetadata
    embedding_model: str
    embedding_dimensions: int
    chunk_count: int


class ChunkStore(Protocol):
    def find_cached_embeddings(self, embedding_keys: list[str]) -> dict[str, CachedEmbedding]: ...

    def upsert_chunks(self, stored_chunks: list[StoredChunk]) -> None: ...

    def delete_chunks_except(self, repository: str, version: str, kept_ids: set[str]) -> int: ...

    def upsert_repository(self, indexed_version: IndexedVersion) -> None: ...


def chunk_id_for(metadata: CloneMetadata, chunk: CodeChunk) -> str:
    return (
        f"{metadata.repository}@{metadata.version}:{chunk.file_path}:"
        f"{chunk.start_line}:{chunk.kind}:{chunk.part_number}"
    )


def repository_id_for(metadata: CloneMetadata) -> str:
    return f"{metadata.repository}@{metadata.version}"


class MongoChunkStore:
    def __init__(self, database: Database) -> None:
        self._chunks = database[CHUNKS_COLLECTION]
        self._repositories = database[REPOSITORIES_COLLECTION]
        self._database = database

    def ensure_indexes(self) -> None:
        self._chunks.create_index([("embedding_key", ASCENDING)])
        self._chunks.create_index([("repository", ASCENDING), ("version", ASCENDING)])

    def find_cached_embeddings(self, embedding_keys: list[str]) -> dict[str, CachedEmbedding]:
        cached_embeddings: dict[str, CachedEmbedding] = {}
        for start in range(0, len(embedding_keys), CACHE_LOOKUP_BATCH_SIZE):
            key_batch = embedding_keys[start : start + CACHE_LOOKUP_BATCH_SIZE]
            documents = self._chunks.find(
                {"embedding_key": {"$in": key_batch}},
                {"embedding_key": 1, "embedding": 1, "embedding_truncated": 1},
            )
            for document in documents:
                cached_embeddings[document["embedding_key"]] = cached_embedding_from(document)
        return cached_embeddings

    def upsert_chunks(self, stored_chunks: list[StoredChunk]) -> None:
        if not stored_chunks:
            return
        operations = [
            ReplaceOne({"_id": stored.chunk_id}, chunk_document(stored), upsert=True)
            for stored in stored_chunks
        ]
        self._chunks.bulk_write(operations, ordered=False)

    def delete_chunks_except(self, repository: str, version: str, kept_ids: set[str]) -> int:
        result = self._chunks.delete_many(
            {"repository": repository, "version": version, "_id": {"$nin": sorted(kept_ids)}}
        )
        return result.deleted_count

    def upsert_repository(self, indexed_version: IndexedVersion) -> None:
        document = repository_document(indexed_version)
        self._repositories.replace_one({"_id": document["_id"]}, document, upsert=True)

    def chunk_collection_statistics(self) -> dict[str, int]:
        """Return document count and sizes in bytes: data, storage on disk, and indexes."""
        statistics = self._database.command("collStats", CHUNKS_COLLECTION)
        return {
            "count": statistics.get("count", 0),
            "size": statistics.get("size", 0),
            "storage_size": statistics.get("storageSize", 0),
            "index_size": statistics.get("totalIndexSize", 0),
        }


def chunk_document(stored: StoredChunk) -> dict[str, Any]:
    chunk = stored.chunk
    return {
        "_id": stored.chunk_id,
        "repository": stored.metadata.repository,
        "version": stored.metadata.version,
        "commit_id": stored.metadata.commit_id,
        "file_path": chunk.file_path,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "kind": str(chunk.kind),
        "name": chunk.name,
        "qualified_name": chunk.qualified_name,
        "parent_class": chunk.parent_class,
        "text": chunk.text,
        "signature": chunk.signature,
        "part_number": chunk.part_number,
        "part_count": chunk.part_count,
        "is_test_file": chunk.is_test_file,
        "contains_redaction": chunk.contains_redaction,
        "embedding_key": stored.embedding_key,
        "embedding": Binary.from_vector(stored.embedding.vector, BinaryVectorDtype.FLOAT32),
        "embedding_truncated": stored.embedding.is_truncated,
    }


def cached_embedding_from(document: dict[str, Any]) -> CachedEmbedding:
    stored_vector = document["embedding"].as_vector()
    return CachedEmbedding(
        vector=list(stored_vector.data),
        is_truncated=bool(document.get("embedding_truncated", False)),
    )


def repository_document(indexed_version: IndexedVersion) -> dict[str, Any]:
    metadata = indexed_version.metadata
    return {
        "_id": repository_id_for(metadata),
        "repository": metadata.repository,
        "version": metadata.version,
        "commit_id": metadata.commit_id,
        "license_spdx_id": metadata.license_spdx_id,
        "license_name": metadata.license_name,
        "embedding_model": indexed_version.embedding_model,
        "embedding_dimensions": indexed_version.embedding_dimensions,
        "chunk_count": indexed_version.chunk_count,
        "indexed_at": datetime.now(UTC),
    }
