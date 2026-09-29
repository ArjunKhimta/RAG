"""The exact text sent to the embedding model for a chunk, its cache key, and a token estimate.

The input is a short header naming the file and the definition, then the chunk's text. The header
puts the location and name into the vector, so a question about Flask's dispatching can match a
method whose body never says "Flask". Later parts of a split definition also get the definition's
signature, which their own text lacks.

The cache key is a SHA-256 hash of the model, dimensions, task type, and input text. Changing any
of them, including the header format, produces a different key, so a stale vector is never reused.
"""

from __future__ import annotations

import hashlib
import json
import math

from retrieval.chunker import ChunkKind, CodeChunk
from retrieval.embedders import DocumentEmbedder

ASCII_CHARACTERS_PER_TOKEN = 3

LAST_ASCII_CODE_POINT = 127

KIND_LABELS = {
    ChunkKind.FUNCTION: "Function",
    ChunkKind.METHOD: "Method",
    ChunkKind.CLASS: "Class",
    ChunkKind.MODULE: "Module-level code",
}


def build_embedding_input(chunk: CodeChunk) -> str:
    header_lines = [f"# File: {chunk.file_path}", f"# {_describe_definition(chunk)}"]
    if chunk.signature:
        header_lines.append(chunk.signature)
    return "\n".join([*header_lines, chunk.text])


def compute_embedding_key(embedder: DocumentEmbedder, input_text: str) -> str:
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


def _describe_definition(chunk: CodeChunk) -> str:
    kind_label = KIND_LABELS[chunk.kind]
    part_label = ""
    if chunk.part_count > 1:
        part_label = f" (part {chunk.part_number} of {chunk.part_count})"
    if chunk.kind == ChunkKind.MODULE:
        return f"{kind_label}{part_label}"
    return f"{kind_label}: {chunk.qualified_name}{part_label}"
