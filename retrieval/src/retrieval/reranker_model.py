"""The local reranker's model files: where they come from, where they live, how they are checked.

Two files from `cross-encoder/ms-marco-MiniLM-L6-v2` on Hugging Face, published by the Sentence
Transformers team, pinned to one commit so they can never change underneath us:

    onnx/model.onnx    the model's weights and computation steps, in ONNX format
    tokenizer.json     the vocabulary that splits text into the model's word pieces

Both are data, not programs. An ONNX file holds numbers and a list of math operations; unlike
PyTorch's pickle-based `.bin` files, loading one cannot run code hidden inside it. Nothing that
ships with the model is ever executed.

Each file's size and SHA-256 are pinned here. The model's hash is the one the Hub records for it;
for `tokenizer.json`, the Hub records a Git blob ID, which was checked against a download before
its SHA-256 was recorded. A download streams into a `.partial` file, stops as soon as it grows past
the expected size, and is renamed into place only once its hash matches, so an unverified file
never sits at the final path. Loading checks the hashes again, so a file altered on disk later is
refused too.

Files live outside git, in `data/models/<owner>/<model>/<revision>/`.
"""

from __future__ import annotations

import hashlib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from retrieval.config import MODELS_DIRECTORY, RERANKER_MODEL_REPOSITORY, RERANKER_MODEL_REVISION

DOWNLOAD_URL_TEMPLATE = "https://huggingface.co/{repository}/resolve/{revision}/{relative_path}"

DOWNLOAD_USER_AGENT = "code-search-retrieval"

DOWNLOAD_TIMEOUT_SECONDS = 60

READ_BLOCK_BYTES = 1024 * 1024

PARTIAL_SUFFIX = ".partial"

DOWNLOAD_SCRIPT = "retrieval/scripts/download_reranker_model.py"


@dataclass(frozen=True)
class ModelFile:
    relative_path: str
    size_bytes: int
    sha256: str


MODEL_ONNX_FILE = ModelFile(
    relative_path="onnx/model.onnx",
    size_bytes=91_011_230,
    sha256="5d3e70fd0c9ff14b9b5169a51e957b7a9c74897afd0a35ce4bd318150c1d4d4a",
)

TOKENIZER_FILE = ModelFile(
    relative_path="tokenizer.json",
    size_bytes=711_396,
    sha256="d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
)

RERANKER_MODEL_FILES = (MODEL_ONNX_FILE, TOKENIZER_FILE)


class RerankerModelError(RuntimeError):
    """Raised when a model file is missing, cannot be downloaded, or fails its check."""


@dataclass(frozen=True)
class RerankerFiles:
    model_path: Path
    tokenizer_path: Path


@dataclass(frozen=True)
class DownloadedFile:
    model_file: ModelFile
    path: Path
    was_downloaded: bool


def reranker_model_directory(models_directory: Path = MODELS_DIRECTORY) -> Path:
    return models_directory / RERANKER_MODEL_REPOSITORY / RERANKER_MODEL_REVISION


def verified_reranker_files(models_directory: Path = MODELS_DIRECTORY) -> RerankerFiles:
    """Return the model's file paths after checking each file's size and SHA-256."""
    model_directory = reranker_model_directory(models_directory)
    for model_file in RERANKER_MODEL_FILES:
        require_verified_file(model_directory / model_file.relative_path, model_file)
    return RerankerFiles(
        model_path=model_directory / MODEL_ONNX_FILE.relative_path,
        tokenizer_path=model_directory / TOKENIZER_FILE.relative_path,
    )


def download_reranker_model(
    models_directory: Path = MODELS_DIRECTORY,
    model_files: tuple[ModelFile, ...] = RERANKER_MODEL_FILES,
) -> list[DownloadedFile]:
    """Download each file that is not already present and verified, then verify it."""
    model_directory = reranker_model_directory(models_directory)
    downloaded_files: list[DownloadedFile] = []
    for model_file in model_files:
        destination = model_directory / model_file.relative_path
        was_downloaded = not is_verified_file(destination, model_file)
        if was_downloaded:
            _download_verified(model_file, destination)
        downloaded_files.append(DownloadedFile(model_file, destination, was_downloaded))
    return downloaded_files


def is_verified_file(path: Path, model_file: ModelFile) -> bool:
    try:
        require_verified_file(path, model_file)
    except RerankerModelError:
        return False
    return True


def require_verified_file(path: Path, model_file: ModelFile) -> None:
    if path.is_symlink():
        raise RerankerModelError(
            f"Reranker file {model_file.relative_path} is a symlink; delete it and run "
            f"{DOWNLOAD_SCRIPT}"
        )
    if not path.is_file():
        raise RerankerModelError(
            f"Reranker file {model_file.relative_path} is missing; run {DOWNLOAD_SCRIPT}"
        )
    actual_size = path.stat().st_size
    if actual_size != model_file.size_bytes:
        raise RerankerModelError(
            f"Reranker file {model_file.relative_path} is {actual_size} bytes, expected "
            f"{model_file.size_bytes}; delete it and run {DOWNLOAD_SCRIPT}"
        )
    if sha256_of_file(path) != model_file.sha256:
        raise RerankerModelError(
            f"Reranker file {model_file.relative_path} does not match its pinned SHA-256; "
            f"delete it and run {DOWNLOAD_SCRIPT}"
        )


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(READ_BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()


def download_url(model_file: ModelFile) -> str:
    return DOWNLOAD_URL_TEMPLATE.format(
        repository=RERANKER_MODEL_REPOSITORY,
        revision=RERANKER_MODEL_REVISION,
        relative_path=model_file.relative_path,
    )


def _download_verified(model_file: ModelFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial_path = destination.with_name(destination.name + PARTIAL_SUFFIX)
    try:
        _stream_to_file(model_file, partial_path)
        if partial_path.stat().st_size != model_file.size_bytes:
            raise RerankerModelError(
                f"Download of {model_file.relative_path} ended at "
                f"{partial_path.stat().st_size} bytes, expected {model_file.size_bytes}"
            )
        if sha256_of_file(partial_path) != model_file.sha256:
            raise RerankerModelError(
                f"Download of {model_file.relative_path} does not match its pinned SHA-256"
            )
        partial_path.replace(destination)
    finally:
        partial_path.unlink(missing_ok=True)


def _stream_to_file(model_file: ModelFile, partial_path: Path) -> None:
    request = urllib.request.Request(
        download_url(model_file), headers={"User-Agent": DOWNLOAD_USER_AGENT}
    )
    received_bytes = 0
    try:
        with (
            urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response,
            partial_path.open("wb") as partial_file,
        ):
            while block := response.read(READ_BLOCK_BYTES):
                received_bytes += len(block)
                if received_bytes > model_file.size_bytes:
                    raise RerankerModelError(
                        f"Download of {model_file.relative_path} grew past the expected "
                        f"{model_file.size_bytes} bytes; stopped"
                    )
                partial_file.write(block)
    except urllib.error.HTTPError as error:
        raise RerankerModelError(
            f"Hugging Face returned HTTP {error.code} for {model_file.relative_path}"
        ) from error
    except urllib.error.URLError as error:
        raise RerankerModelError(f"Hugging Face could not be reached: {error.reason}") from error
