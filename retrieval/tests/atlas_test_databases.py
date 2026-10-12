"""Emptying a throwaway test database without the right to drop a database.

The local Atlas user may read and write any database but not drop one (lowered from Atlas admin on
2026-10-12, so the key on the laptop cannot manage users, network access, or the cluster). Tests
delete every collection instead; MongoDB removes a database once it holds no collections.
"""

from __future__ import annotations

from pymongo import MongoClient

SYSTEM_COLLECTION_PREFIX = "system."


def empty_database(mongo_client: MongoClient, database_name: str) -> None:
    database = mongo_client[database_name]
    for collection_name in database.list_collection_names():
        if not collection_name.startswith(SYSTEM_COLLECTION_PREFIX):
            database.drop_collection(collection_name)
