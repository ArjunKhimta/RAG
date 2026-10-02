"""The exact text sent to the embedding model for a chunk or question, its cache key, and a
token estimate.

The input is a short header naming the file and the definition, then the chunk's text. The header
puts the location and name into the vector, so a question about Flask's dispatching can match a
method whose body never says "Flask". Later parts of a split definition also get the definition's
signature, which their own text lacks. The reranker reads the same text, rebuilt from a stored
search result by `build_result_input`, so both models see a chunk the same way.

The cache key is a SHA-256 hash of the model, dimensions, task type, and input text. Changing any
of them, including the header format, produces a different key, so a stale vector is never reused.
Chunks and questions share the recipe; their task types differ, so their keys never collide.

A question is cleaned by collapsing runs of whitespace into single spaces and nothing more. Case is
kept, because `Flask` and `flask`, or `URL` and `url`, can name different things in code.
"""

from __future__ import annotations

import hashlib
import json
import math

from retrieval.chunker import ChunkKind, CodeChunk
from retrieval.embedders import EmbeddingIdentity
from retrieval.search_results import SearchResult

ASCII_CHARACTERS_PER_TOKEN = 3

LAST_ASCII_CODE_POINT = 127

KIND_LABELS = {
    ChunkKind.FUNCTION: "Function",
    ChunkKind.METHOD: "Method",
    ChunkKind.CLASS: "Class",
    ChunkKind.MODULE: "Module-level code",
}


def build_embedding_input(chunk: CodeChunk) -> str:
    return _format_input(
        file_path=chunk.file_path,
        kind=chunk.kind,
        qualified_name=chunk.qualified_name,
        part_number=chunk.part_number,
        part_count=chunk.part_count,
        signature=chunk.signature,
        text=chunk.text,
    )


def build_result_input(result: SearchResult) -> str:
    return _format_input(
        file_path=result.file_path,
        kind=ChunkKind(result.kind),
        qualified_name=result.qualified_name,
        part_number=result.part_number,
        part_count=result.part_count,
        signature=result.signature,
        text=result.text,
    )


def normalize_question(question: str) -> str:
    return " ".join(question.split())


def compute_embedding_key(embedder: EmbeddingIdentity, input_text: str) -> str:
    """Hash everything that determines the vector; JSON keeps the four parts unambiguous."""
    key_parts = [embedder.model_id, embedder.dimensions, embedder.task_type, input_text]
    serialized_parts = json.dumps(key_parts, ensure_ascii=False)
    return hashlib.sha256(serialized_parts.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    """Estimate tokens pessimistically: one per 3 ASCII characters, one per other character.

    Code measured 3.2 to 4.1 characters per token, and text in scripts such as Chinese can be
    close to one character per token, so this errs high on both.
    """
    non_ascii_count = sum(1 for character in text if ord(character) > LAST_ASCII_CODE_POINT)
    ascii_count = len(text) - non_ascii_count
    return math.ceil(ascii_count / ASCII_CHARACTERS_PER_TOKEN) + non_ascii_count


def _format_input(
    file_path: str,
    kind: ChunkKind,
    qualified_name: str,
    part_number: int,
    part_count: int,
    signature: str | None,
    text: str,
) -> str:
    definition = _describe_definition(kind, qualified_name, part_number, part_count)
    header_lines = [f"# File: {file_path}", f"# {definition}"]
    if signature:
        header_lines.append(signature)
    return "\n".join([*header_lines, text])


def _describe_definition(
    kind: ChunkKind, qualified_name: str, part_number: int, part_count: int
) -> str:
    kind_label = KIND_LABELS[kind]
    part_label = ""
    if part_count > 1:
        part_label = f" (part {part_number} of {part_count})"
    if kind == ChunkKind.MODULE:
        return f"{kind_label}{part_label}"
    return f"{kind_label}: {qualified_name}{part_label}"
