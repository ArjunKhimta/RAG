"""Constructors for the two external services the retrieval pipeline depends on."""

from __future__ import annotations

from google import genai
from pymongo import MongoClient

from retrieval.config import (
    GEMINI_API_KEY_VARIABLE,
    MONGODB_URI_VARIABLE,
    get_required_env,
)

SERVER_SELECTION_TIMEOUT_MS = 5000


def build_mongo_client() -> MongoClient:
    """Return a MongoDB client that gives up quickly when the cluster is unreachable.

    The driver default is thirty seconds, which turns a typo in the connection URI into a long
    unexplained pause. Five seconds is enough for an Atlas handshake over a normal connection.
    """
    connection_uri = get_required_env(MONGODB_URI_VARIABLE)
    return MongoClient(connection_uri, serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS)


def build_gemini_client() -> genai.Client:
    api_key = get_required_env(GEMINI_API_KEY_VARIABLE)
    return genai.Client(api_key=api_key)
