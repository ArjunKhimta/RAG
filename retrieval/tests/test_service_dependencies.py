from __future__ import annotations

import json
import subprocess
import sys

import pytest

from retrieval.asking import load_local_reranker
from retrieval.reranker_model import RerankerLibrariesMissingError

SERVICE_MODULES = [
    "retrieval.service",
    "retrieval.service.app",
    "retrieval.asking",
    "retrieval.answer_sources",
    "retrieval.indexing_jobs",
    "retrieval.repository_indexing",
    "retrieval.repository_overview",
    "retrieval.reranking",
]

RERANKER_ONLY_MODULES = ["onnxruntime", "tokenizers", "numpy", "retrieval.cross_encoder"]

LOADED_MODULES_SCRIPT = """
import importlib, json, sys
service_modules, watched = json.loads(sys.argv[1]), json.loads(sys.argv[2])
for name in service_modules:
    importlib.import_module(name)
print(json.dumps(sorted(set(watched) & set(sys.modules))))
"""


def test_nothing_the_service_imports_loads_the_reranker_libraries():
    """The service's image leaves out ONNX Runtime, tokenizers, and numpy, so importing any
    module the service runs must not load them, or the service would fail to start."""
    finished = subprocess.run(
        [
            sys.executable,
            "-c",
            LOADED_MODULES_SCRIPT,
            json.dumps(SERVICE_MODULES),
            json.dumps(RERANKER_ONLY_MODULES),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(finished.stdout) == []


def test_building_a_reranker_without_its_libraries_says_what_to_install(monkeypatch):
    monkeypatch.setitem(sys.modules, "retrieval.cross_encoder", None)

    with pytest.raises(RerankerLibrariesMissingError, match="requirements-reranker.txt"):
        load_local_reranker()
