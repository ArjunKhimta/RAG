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

VECTOR_SEARCH_CANDIDATE_MULTIPLIER = 20

DEFAULT_SEARCH_LIMIT = 10

MAX_SEARCH_LIMIT = 100


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
