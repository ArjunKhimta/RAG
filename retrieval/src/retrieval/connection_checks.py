"""Connectivity checks for MongoDB Atlas and the Gemini API.

The checks live in the package rather than in the script so the integration test can run exactly
the code the script runs. Every failure path funnels through `redact_exception`, so a driver
error carrying a connection URI or an API key is scrubbed before it becomes a result detail.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import GEMINI_EMBEDDING_MODEL, MissingConfigError
from retrieval.redaction import redact_exception

MAXIMUM_MODELS_EXAMINED = 500

MODEL_NAME_PREFIX = "models/"


@dataclass(frozen=True)
class CheckResult:
    service_name: str
    passed: bool
    detail: str
    elapsed_ms: float


def check_mongodb() -> CheckResult:
    """Confirm the Atlas cluster answers a ping with the configured connection URI."""
    started_at = time.perf_counter()
    try:
        client = build_mongo_client()
        try:
            client.admin.command("ping")
            detail = f"ping succeeded, server {_read_server_version(client)}"
        finally:
            client.close()
    except MissingConfigError as error:
        return _failure("MongoDB", error.args[0], started_at)
    except Exception as error:
        return _failure("MongoDB", redact_exception(error), started_at)
    return _success("MongoDB", detail, started_at)


def check_gemini() -> CheckResult:
    """Confirm the Gemini API key authenticates and the embedding model is reachable.

    Listing models costs no tokens, so it is the cheapest proof that the key is valid. The
    listing does not always include embedding models, so when the configured model is absent the
    check falls back to embedding a single short string.
    """
    started_at = time.perf_counter()
    try:
        client = build_gemini_client()
        available_model_names = set(_iterate_model_names(client))
        if GEMINI_EMBEDDING_MODEL in available_model_names:
            detail = f"authenticated, {GEMINI_EMBEDDING_MODEL} listed"
        else:
            client.models.embed_content(model=GEMINI_EMBEDDING_MODEL, contents="ping")
            detail = f"authenticated, {GEMINI_EMBEDDING_MODEL} answered a test embedding"
    except MissingConfigError as error:
        return _failure("Gemini", error.args[0], started_at)
    except Exception as error:
        return _failure("Gemini", redact_exception(error), started_at)
    return _success("Gemini", detail, started_at)


def run_all_checks() -> list[CheckResult]:
    return [check_mongodb(), check_gemini()]


def _read_server_version(client) -> str:
    """Return the cluster version, or a placeholder when the deployment withholds build info."""
    try:
        return str(client.server_info()["version"])
    except Exception:
        return "version unavailable"


def _iterate_model_names(client) -> Iterator[str]:
    for examined_count, model in enumerate(client.models.list()):
        if examined_count >= MAXIMUM_MODELS_EXAMINED:
            return
        model_name = getattr(model, "name", None)
        if model_name:
            yield model_name.removeprefix(MODEL_NAME_PREFIX)


def _success(service_name: str, detail: str, started_at: float) -> CheckResult:
    return CheckResult(service_name, True, detail, _elapsed_ms(started_at))


def _failure(service_name: str, detail: str, started_at: float) -> CheckResult:
    return CheckResult(service_name, False, detail, _elapsed_ms(started_at))


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000
