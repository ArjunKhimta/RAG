"""Download and verify the local reranker's two model files from Hugging Face.

Run from the repository root with the virtual environment active:

    python retrieval/scripts/download_reranker_model.py

Fetches `onnx/model.onnx` (about 91 MB) and `tokenizer.json` from
`cross-encoder/ms-marco-MiniLM-L6-v2` at a pinned commit into `data/models/`, and keeps each one
only if its size and SHA-256 match the values pinned in `reranker_model.py`. Files already present
and verified are not downloaded again. Needs no credentials. Exits 0 when both files are in place
and verified, and 1 otherwise. Every printed line passes through the redaction module.
"""

from __future__ import annotations

import sys
import time

from retrieval.config import RERANKER_MODEL_REPOSITORY, RERANKER_MODEL_REVISION
from retrieval.redaction import redact
from retrieval.reranker_model import (
    RerankerModelError,
    download_reranker_model,
    reranker_model_directory,
)

COMMIT_ID_DISPLAY_LENGTH = 12

BYTES_PER_MEGABYTE = 1_000_000

LABEL_WIDTH = 20


def main() -> int:
    revision = RERANKER_MODEL_REVISION[:COMMIT_ID_DISPLAY_LENGTH]
    _print(f"Model       {RERANKER_MODEL_REPOSITORY} at {revision}")
    _print(f"Folder      {reranker_model_directory()}")
    _print("")
    started = time.perf_counter()
    try:
        downloaded_files = download_reranker_model()
    except RerankerModelError as error:
        _print(f"Failed: {error}")
        return 1
    for downloaded in downloaded_files:
        status = "downloaded" if downloaded.was_downloaded else "already present"
        size_megabytes = downloaded.model_file.size_bytes / BYTES_PER_MEGABYTE
        label = downloaded.model_file.relative_path.ljust(LABEL_WIDTH)
        _print(f"  {label}{size_megabytes:6.1f} MB  {status}, SHA-256 verified")
    _print("")
    _print(f"Finished in {time.perf_counter() - started:.1f} s")
    return 0


def _print(line: str) -> None:
    print(redact(line))


if __name__ == "__main__":
    sys.exit(main())
