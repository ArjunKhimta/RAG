"""Name the programming languages of files the indexer does not read.

Only Python is indexed. Files in other programming languages are counted, never read, so answers
from a repository whose core is in C or Rust can say that only its Python was searched. Each
language is known by its file endings; documentation, data, and configuration files are not
programming languages here and are not counted.

`.h` counts as C although C++ uses it too; the count says what was left out, not exactly which
compiler would read it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cxx": "C++",
    ".hh": "C++",
    ".hpp": "C++",
    ".hxx": "C++",
    ".pyx": "Cython",
    ".pxd": "Cython",
    ".rs": "Rust",
    ".go": "Go",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".cs": "C#",
    ".swift": "Swift",
    ".m": "Objective-C",
    ".mm": "Objective-C++",
    ".rb": "Ruby",
    ".php": "PHP",
    ".lua": "Lua",
    ".f": "Fortran",
    ".f90": "Fortran",
    ".f95": "Fortran",
    ".jl": "Julia",
    ".r": "R",
    ".sh": "Shell",
    ".bash": "Shell",
    ".zig": "Zig",
}


@dataclass(frozen=True)
class OtherLanguageCount:
    language: str
    files: int


def language_of(file_name: str) -> str | None:
    """Return the programming language a file name's ending names, or None for anything else."""
    suffix = PurePosixPath(file_name).suffix.lower()
    return LANGUAGE_BY_SUFFIX.get(suffix)


def summarize_languages(languages: Iterable[str]) -> tuple[OtherLanguageCount, ...]:
    """Count each language once per file, most files first, then by name."""
    counts = Counter(languages)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return tuple(OtherLanguageCount(language=language, files=files) for language, files in ordered)


def other_language_documents(counts: tuple[OtherLanguageCount, ...]) -> list[dict[str, Any]]:
    return [{"language": count.language, "files": count.files} for count in counts]


def describe_counts(counts: tuple[OtherLanguageCount, ...]) -> str:
    """Write the counts as prose: "40 JavaScript files and 1 Shell file"."""
    phrases = [f"{count.files:,} {count.language} {_file_word(count.files)}" for count in counts]
    if len(phrases) <= 1:
        return "".join(phrases)
    return ", ".join(phrases[:-1]) + " and " + phrases[-1]


def _file_word(files: int) -> str:
    return "file" if files == 1 else "files"
