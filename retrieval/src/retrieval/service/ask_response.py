"""The JSON reply of `POST /ask`, built from a finished ask.

The reply keeps the answer's structure: each sentence with its own citations, so the page can show
a citation beside the sentence it supports, and the joined text with markers such as [1]. Sources
are listed by location only, never with their code. Code appears only as snippets of the cited
lines, at most 12 lines each with a link to the rest at the indexed commit, so no answer carries a
whole file. The repository block names the indexed commit and its license, with a link to the
license file at that commit, since every answer must carry the license. "No code matched" is a
normal reply, with empty lists, not an error.
"""

from __future__ import annotations

from typing import Any

from retrieval.answer_generation import AnswerSentence, Citation, GeneratedAnswer
from retrieval.asking import AskRun
from retrieval.search_results import SearchResult
from retrieval.snippets import (
    cited_snippet,
    github_file_link,
    github_line_link,
    unique_citations,
)

NO_SOURCES_ANSWER = "No code in this repository version matched the question."


def ask_response(ask_run: AskRun, repository: str, version: str) -> dict[str, Any]:
    generated = ask_run.generated
    commit_id = ask_run.repository_record["commit_id"]
    return {
        "found_answer": generated.found_answer,
        "answer": generated.answer,
        "sentences": [_sentence(sentence) for sentence in generated.sentences],
        "sources": [
            _source(number, source, repository, commit_id)
            for number, source in enumerate(generated.sources, start=1)
        ],
        "snippets": [
            _snippet(citation, generated, repository, commit_id)
            for citation in unique_citations(generated.citations)
        ],
        "repository": repository_block(ask_run.repository_record, repository, version),
        "usage": _usage(ask_run),
        "timings_ms": _rounded_timings(ask_run.timings),
    }


def no_sources_response(
    repository_record: dict[str, Any], repository: str, version: str
) -> dict[str, Any]:
    return {
        "found_answer": False,
        "answer": NO_SOURCES_ANSWER,
        "sentences": [],
        "sources": [],
        "snippets": [],
        "repository": repository_block(repository_record, repository, version),
        "usage": None,
        "timings_ms": {},
    }


def repository_block(
    repository_record: dict[str, Any], repository: str, version: str
) -> dict[str, Any]:
    commit_id = repository_record["commit_id"]
    license_path = repository_record["license_path"]
    return {
        "name": repository,
        "version": version,
        "commit_id": commit_id,
        "license": {
            "spdx_id": repository_record["license_spdx_id"],
            "name": repository_record.get("license_name"),
            "path": license_path,
            "link": github_file_link(repository, commit_id, license_path),
        },
    }


def _sentence(sentence: AnswerSentence) -> dict[str, Any]:
    return {
        "text": sentence.text,
        "citations": [_citation(citation) for citation in sentence.citations],
    }


def _citation(citation: Citation) -> dict[str, int]:
    return {
        "source": citation.source_number,
        "start_line": citation.start_line,
        "end_line": citation.end_line,
    }


def _source(number: int, source: SearchResult, repository: str, commit_id: str) -> dict[str, Any]:
    return {
        "number": number,
        "file_path": source.file_path,
        "kind": source.kind,
        "qualified_name": source.qualified_name,
        "start_line": source.start_line,
        "end_line": source.end_line,
        "link": github_line_link(
            repository, commit_id, source.file_path, source.start_line, source.end_line
        ),
    }


def _snippet(
    citation: Citation, generated: GeneratedAnswer, repository: str, commit_id: str
) -> dict[str, Any]:
    source = generated.sources[citation.source_number - 1]
    snippet = cited_snippet(source, citation)
    return {
        **_citation(citation),
        "link": github_line_link(
            repository, commit_id, source.file_path, citation.start_line, citation.end_line
        ),
        "is_outline": snippet.is_outline,
        "lines": [{"number": line.number, "text": line.text} for line in snippet.lines],
        "more_lines": snippet.more_line_count,
    }


def _rounded_timings(timings: dict[str, float]) -> dict[str, int]:
    return {stage: round(milliseconds) for stage, milliseconds in timings.items()}


def _usage(ask_run: AskRun) -> dict[str, Any]:
    reply = ask_run.generated.reply
    return {
        "embedding": str(ask_run.embedding_use),
        "answer_requests": reply.attempt_count,
        "prompt_tokens": reply.prompt_tokens,
        "output_tokens": reply.output_tokens,
    }
