"""Environment configuration for the retrieval service.

Values are referenced by variable name only. Nothing here prints, logs, or returns a credential
in an error message, and every value read through `get_required_env` is registered with the
redaction module so it cannot surface later inside someone else's exception text.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from retrieval.redaction import register_secret

RETRIEVAL_ROOT = Path(__file__).resolve().parents[2]

ENV_FILE = RETRIEVAL_ROOT / ".env"

REPOSITORIES_DIRECTORY = RETRIEVAL_ROOT.parent / "data" / "repos"

MODELS_DIRECTORY = RETRIEVAL_ROOT.parent / "data" / "models"

MAX_REPOSITORY_BYTES = 100 * 1024 * 1024

MAX_FILE_BYTES = 300 * 1024

MONGODB_URI_VARIABLE = "MONGODB_URI"

GEMINI_API_KEY_VARIABLE = "GEMINI_API_KEY"

GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"

EMBEDDING_DIMENSIONS = 768

EMBEDDING_INPUT_TOKEN_LIMIT = 2048

EMBEDDING_BATCH_SIZE = 100

EMBEDDING_REQUESTS_PER_MINUTE = 100

EMBEDDING_TOKENS_PER_MINUTE = 30_000

MONGODB_DATABASE = "code_search"

VECTOR_INDEX_NAME = "chunk_embedding_vector"

KEYWORD_INDEX_NAME = "chunk_keywords"

CODE_ANALYZER_NAME = "code"

NAME_FIELD_BOOST = 3

RRF_K = 60

HYBRID_CANDIDATE_DEPTH = 30

VECTOR_SEARCH_CANDIDATE_MULTIPLIER = 20

DEFAULT_SEARCH_LIMIT = 10

MAX_SEARCH_LIMIT = 100

QUERY_EMBEDDING_TTL_SECONDS = 30 * 24 * 60 * 60

RERANKER_MODEL_REPOSITORY = "cross-encoder/ms-marco-MiniLM-L6-v2"

RERANKER_MODEL_REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"

RERANKER_MAX_TOKENS = 512

RERANKER_BATCH_SIZE = 1

RERANK_CANDIDATE_COUNT = 30

RERANK_RESULT_COUNT = 5

GRAPH_NEIGHBOR_CANDIDATE_LIMIT = 30

GRAPH_NEIGHBOR_RESULT_COUNT = 3

GEMINI_ANSWER_MODEL = "gemini-3.5-flash-lite"

ANSWER_MAX_OUTPUT_TOKENS = 4096

ANSWER_REQUESTS_PER_MINUTE = 15

ANSWER_TOKENS_PER_MINUTE = 250_000

ANSWER_MAX_ATTEMPTS = 3

SERVICE_TOKEN_VARIABLE = "RETRIEVAL_SERVICE_TOKEN"

MINIMUM_SERVICE_TOKEN_LENGTH = 32

SERVICE_MAX_REQUEST_BYTES = 16 * 1024


class MissingConfigError(RuntimeError):
    """Raised when a required environment variable is absent or empty."""

    def __init__(self, variable_name: str) -> None:
        super().__init__(f"Required environment variable {variable_name} is not set")
        self.variable_name = variable_name


def load_environment() -> None:
    """Load `retrieval/.env` into the process environment without overriding existing values."""
    load_dotenv(ENV_FILE)


def get_required_env(variable_name: str) -> str:
    """Return the value of `variable_name`, or raise `MissingConfigError` naming the variable."""
    value = os.environ.get(variable_name)
    if not value:
        raise MissingConfigError(variable_name)
    register_secret(value)
    return value
