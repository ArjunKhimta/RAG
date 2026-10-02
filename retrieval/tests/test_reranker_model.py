from __future__ import annotations

import hashlib
import io
import urllib.error
from pathlib import Path

import pytest

from retrieval import reranker_model
from retrieval.config import RERANKER_MODEL_REPOSITORY, RERANKER_MODEL_REVISION
from retrieval.reranker_model import (
    MODEL_ONNX_FILE,
    ModelFile,
    RerankerModelError,
    download_reranker_model,
    download_url,
    require_verified_file,
    reranker_model_directory,
)

GOOD_CONTENT = b"pretend model weights"

GOOD_FILE = ModelFile(
    relative_path="onnx/model.onnx",
    size_bytes=len(GOOD_CONTENT),
    sha256=hashlib.sha256(GOOD_CONTENT).hexdigest(),
)


class FakeOpener:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.requested_urls: list[str] = []

    def __call__(self, request, timeout):
        self.requested_urls.append(request.full_url)
        return io.BytesIO(self.content)


def _install_opener(monkeypatch, content: bytes) -> FakeOpener:
    opener = FakeOpener(content)
    monkeypatch.setattr(reranker_model.urllib.request, "urlopen", opener)
    return opener


def _destination(models_directory: Path) -> Path:
    return reranker_model_directory(models_directory) / GOOD_FILE.relative_path


def test_files_live_under_the_pinned_repository_and_revision(tmp_path):
    assert reranker_model_directory(tmp_path) == (
        tmp_path / RERANKER_MODEL_REPOSITORY / RERANKER_MODEL_REVISION
    )


def test_downloads_come_from_hugging_face_at_the_pinned_revision():
    assert download_url(MODEL_ONNX_FILE) == (
        f"https://huggingface.co/{RERANKER_MODEL_REPOSITORY}/resolve/"
        f"{RERANKER_MODEL_REVISION}/onnx/model.onnx"
    )


def test_a_matching_download_is_moved_into_place(tmp_path, monkeypatch):
    _install_opener(monkeypatch, GOOD_CONTENT)

    downloaded = download_reranker_model(tmp_path, (GOOD_FILE,))

    destination = _destination(tmp_path)
    assert destination.read_bytes() == GOOD_CONTENT
    assert downloaded[0].was_downloaded
    assert not destination.with_name("model.onnx.partial").exists()


def test_a_download_with_the_wrong_hash_is_deleted_and_refused(tmp_path, monkeypatch):
    tampered_content = b"pretend model weightZ"
    _install_opener(monkeypatch, tampered_content)

    with pytest.raises(RerankerModelError, match="does not match its pinned SHA-256"):
        download_reranker_model(tmp_path, (GOOD_FILE,))

    destination = _destination(tmp_path)
    assert not destination.exists()
    assert not destination.with_name("model.onnx.partial").exists()


def test_a_download_larger_than_expected_is_stopped_and_deleted(tmp_path, monkeypatch):
    _install_opener(monkeypatch, GOOD_CONTENT + b"extra bytes")

    with pytest.raises(RerankerModelError, match="grew past the expected"):
        download_reranker_model(tmp_path, (GOOD_FILE,))

    destination = _destination(tmp_path)
    assert not destination.exists()
    assert not destination.with_name("model.onnx.partial").exists()


def test_a_download_shorter_than_expected_is_refused(tmp_path, monkeypatch):
    _install_opener(monkeypatch, GOOD_CONTENT[:-1])

    with pytest.raises(RerankerModelError, match="expected"):
        download_reranker_model(tmp_path, (GOOD_FILE,))

    assert not _destination(tmp_path).exists()


def test_an_already_verified_file_is_not_downloaded_again(tmp_path, monkeypatch):
    destination = _destination(tmp_path)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(GOOD_CONTENT)
    opener = _install_opener(monkeypatch, GOOD_CONTENT)

    downloaded = download_reranker_model(tmp_path, (GOOD_FILE,))

    assert opener.requested_urls == []
    assert not downloaded[0].was_downloaded


def test_an_altered_file_on_disk_is_downloaded_again(tmp_path, monkeypatch):
    destination = _destination(tmp_path)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"pretend model weightZ")
    opener = _install_opener(monkeypatch, GOOD_CONTENT)

    downloaded = download_reranker_model(tmp_path, (GOOD_FILE,))

    assert len(opener.requested_urls) == 1
    assert downloaded[0].was_downloaded
    assert destination.read_bytes() == GOOD_CONTENT


def test_an_http_error_becomes_a_reranker_model_error(tmp_path, monkeypatch):
    def failing_opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(reranker_model.urllib.request, "urlopen", failing_opener)

    with pytest.raises(RerankerModelError, match="HTTP 404"):
        download_reranker_model(tmp_path, (GOOD_FILE,))


def test_a_missing_file_is_refused_with_the_download_command(tmp_path):
    with pytest.raises(RerankerModelError, match="missing; run .*download_reranker_model.py"):
        require_verified_file(tmp_path / "model.onnx", GOOD_FILE)


def test_a_file_of_the_wrong_size_is_refused(tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(GOOD_CONTENT + b"!")

    with pytest.raises(RerankerModelError, match="bytes, expected"):
        require_verified_file(path, GOOD_FILE)


def test_a_file_of_the_right_size_but_wrong_hash_is_refused(tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(b"pretend model weightZ")

    with pytest.raises(RerankerModelError, match="does not match its pinned SHA-256"):
        require_verified_file(path, GOOD_FILE)


def test_a_symlink_is_refused_even_if_its_target_matches(tmp_path):
    target = tmp_path / "elsewhere.onnx"
    target.write_bytes(GOOD_CONTENT)
    link = tmp_path / "model.onnx"
    link.symlink_to(target)

    with pytest.raises(RerankerModelError, match="is a symlink"):
        require_verified_file(link, GOOD_FILE)
