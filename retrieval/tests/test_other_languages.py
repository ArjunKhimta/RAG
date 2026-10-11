from __future__ import annotations

import pytest

from retrieval.other_languages import (
    OtherLanguageCount,
    describe_counts,
    language_of,
    other_language_documents,
    summarize_languages,
)


@pytest.mark.parametrize(
    "file_name, language",
    [
        ("_speedups.c", "C"),
        ("module.h", "C"),
        ("engine.CPP", "C++"),
        ("fast.pyx", "Cython"),
        ("lib.rs", "Rust"),
        ("main.go", "Go"),
        ("app.tsx", "TypeScript"),
        ("build.sh", "Shell"),
    ],
)
def test_a_programming_language_is_known_by_its_file_ending(file_name, language):
    assert language_of(file_name) == language


@pytest.mark.parametrize(
    "file_name", ["README.md", "pyproject.toml", "data.json", "Makefile", "logo.png", "app.py"]
)
def test_documents_data_configuration_and_python_are_not_other_languages(file_name):
    assert language_of(file_name) is None


def test_counts_are_ordered_by_most_files_then_by_name():
    counts = summarize_languages(["Rust", "C", "C", "Go", "C", "Rust"])

    assert counts == (
        OtherLanguageCount(language="C", files=3),
        OtherLanguageCount(language="Rust", files=2),
        OtherLanguageCount(language="Go", files=1),
    )


def test_counts_are_stored_as_plain_documents():
    counts = (OtherLanguageCount(language="C", files=214),)

    assert other_language_documents(counts) == [{"language": "C", "files": 214}]


@pytest.mark.parametrize(
    "counts, prose",
    [
        ((), ""),
        ((OtherLanguageCount("Shell", 1),), "1 Shell file"),
        (
            (OtherLanguageCount("C", 214), OtherLanguageCount("C++", 12)),
            "214 C files and 12 C++ files",
        ),
        (
            (
                OtherLanguageCount("C", 1200),
                OtherLanguageCount("Cython", 3),
                OtherLanguageCount("Shell", 1),
            ),
            "1,200 C files, 3 Cython files and 1 Shell file",
        ),
    ],
)
def test_counts_read_as_prose(counts, prose):
    assert describe_counts(counts) == prose
