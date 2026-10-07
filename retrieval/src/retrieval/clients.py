"""Constructors for the two external services the retrieval pipeline depends on."""

from __future__ import annotations

from google import genai
from google.genai import types
from pymongo import MongoClient

from retrieval.config import (
    GEMINI_API_KEY_VARIABLE,
    GEMINI_REQUEST_TIMEOUT_SECONDS,
    MONGODB_URI_VARIABLE,
    get_required_env,
)

SERVER_SELECTION_TIMEOUT_MS = 20000

MILLISECONDS_PER_SECOND = 1000


def build_mongo_client() -> MongoClient:
    """Return a MongoDB client that gives up within twenty seconds when the cluster is unreachable.

    The driver default is thirty seconds, which turns a typo in the connection URI into a long
    unexplained pause. Five seconds was too short: a free-tier Atlas cluster took about eleven
    seconds to answer after being idle.
    """
    connection_uri = get_required_env(MONGODB_URI_VARIABLE)
    return MongoClient(connection_uri, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS)


def build_gemini_client() -> genai.Client:
    """Return a Gemini client whose every call gives up after thirty seconds.

    Without a limit a call can wait indefinitely, and the service answers one question at a time,
    so one stuck call held up every question and search behind it (measured on 2026-10-07, when
    Gemini took up to 149 s per answer). Normal calls take 1.4 to 2.0 s for an answer and a few
    seconds for a batch of embeddings, so thirty seconds only cuts off calls that are already
    stuck; the callers treat a call cut off this way as a temporary failure.
    """
    api_key = get_required_env(GEMINI_API_KEY_VARIABLE)
    timeout_milliseconds = GEMINI_REQUEST_TIMEOUT_SECONDS * MILLISECONDS_PER_SECOND
    return genai.Client(
        api_key=api_key, http_options=types.HttpOptions(timeout=timeout_milliseconds)
    )
