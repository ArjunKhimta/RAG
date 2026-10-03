"""Answer a question about an indexed repository version, citing exact files and lines.

Run from the repository root with the virtual environment active:

    python retrieval/scripts/ask_repository.py "How does Flask sign the session cookie?" \
        --repository pallets/flask --version 3.1.3

A question that is a single code name, such as `url_for`, goes to keyword search, whose top 5 are
used directly; anything else goes to hybrid search, whose top 30 are reranked by the local
cross-encoder to the best 5. `--no-router` sends every question to hybrid search. Gemini then
writes a short answer from the 5 sources with markers such as [2]. Every reference is checked
against the code that was sent; a reply citing anything else is rejected, never shown. Prints the
answer, then for each citation the file, lines, a short snippet, and a link to those lines on
GitHub at the indexed commit, then the license, the tokens used, and the time for each stage.
`--exclude-tests` leaves test files out of the search. `--expand` also adds the 3 callers or
callees of those sources that the cross-encoder rates best for the question, found through the call
graph; it is off by default until measured.

Costs one generation request, plus one embedding request for a hybrid-route question not asked
before; a code name needs no embedding. A
temporary Gemini failure is retried up to twice, and the report says how many requests were made.
Stays under the per-minute generation limits and stops with a clear message when a daily quota is
used up. Run `download_reranker_model.py` once first. Exits 0 when an answer is shown, including one
saying the code does not contain the answer, and 1 on any refusal, rejection, or failure. Every
printed line passes through the redaction module.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
import time
from dataclasses import dataclass
from typing import Any

from pymongo.database import Database
from pymongo.errors import PyMongoError

from retrieval.answer_generation import (
    AnswerRejectedError,
    Citation,
    GeminiAnswerModel,
    GeneratedAnswer,
    generate_answer,
    is_outline,
    source_code_lines,
)
from retrieval.answer_sources import ExpandedSources, expand_sources, find_sources
from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore
from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import (
    ANSWER_REQUESTS_PER_MINUTE,
    ANSWER_TOKENS_PER_MINUTE,
    GEMINI_ANSWER_MODEL,
    KEYWORD_INDEX_NAME,
    MONGODB_DATABASE,
    RERANK_CANDIDATE_COUNT,
    VECTOR_INDEX_NAME,
    MissingConfigError,
    load_environment,
)
from retrieval.embedders import GeminiQueryEmbedder
from retrieval.gemini_errors import GeminiRequestError
from retrieval.graph_expansion import require_call_graph
from retrieval.query_cache import CachingQueryEmbedder, MongoQueryEmbeddingStore
from retrieval.query_router import (
    QueryRoute,
    RouteDecision,
    hybrid_without_router,
    route_query,
)
from retrieval.rate_limiter import RateLimiter
from retrieval.redaction import redact
from retrieval.reranker_model import RerankerModelError, verified_reranker_files
from retrieval.reranking import CrossEncoderScorer, QuestionTooLongError
from retrieval.search_indexes import require_queryable_index
from retrieval.search_results import SearchRefusedError, SearchResult, require_indexed_version
from retrieval.vector_search import require_searchable_version

MILLISECONDS_PER_SECOND = 1000

COMMIT_ID_DISPLAY_LENGTH = 12

ANSWER_WRAP_WIDTH = 96

MAX_SNIPPET_LINES = 12

LABEL_WIDTH = 28

GITHUB_LINE_LINK = "https://github.com/{repository}/blob/{commit_id}/{file_path}#L{start}-L{end}"


class _NoSourcesFound(Exception):
    """Raised when the search returns nothing to answer from."""


@dataclass(frozen=True)
class AskRun:
    generated: GeneratedAnswer
    timings: dict[str, float]
    embedding_status: str
    expansion: ExpandedSources | None
    route: RouteDecision


def main() -> int:
    arguments = _parse_arguments()
    load_environment()
    try:
        mongo_client = build_mongo_client()
        database = mongo_client[MONGODB_DATABASE]
        store = MongoChunkStore(database)
        repository_record = store.find_repository_record(arguments.repository, arguments.version)
        require_indexed_version(repository_record, arguments.repository, arguments.version)
        ask_run = _ask(arguments, database, repository_record)
    except (SearchRefusedError, QuestionTooLongError) as error:
        _print(f"Refused: {error}")
        return 1
    except AnswerRejectedError as error:
        _print("Rejected: Gemini's answer was not shown because it failed the reference check:")
        for problem in error.problems:
            _print(f"  - {problem}")
        return 1
    except GeminiRequestError as error:
        if error.is_daily_quota_exhausted:
            _print("Stopped: a daily Gemini quota is used up; try again after the reset.")
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    except (MissingConfigError, PyMongoError, RerankerModelError) as error:
        _print(f"Failed: {type(error).__name__}: {error}")
        return 1
    except _NoSourcesFound:
        _print("No code matched the question, so there is nothing to answer from.")
        return 0
    for line in _report_lines(arguments, repository_record, ask_run):
        _print(line)
    return 0


def _ask(
    arguments: argparse.Namespace, database: Database, repository_record: dict[str, Any]
) -> AskRun:
    if arguments.expand:
        require_call_graph(repository_record, arguments.repository, arguments.version)
    query_embedding_store = MongoQueryEmbeddingStore(database)
    query_embedding_store.ensure_indexes()
    gemini_client = build_gemini_client()
    embedder = CachingQueryEmbedder(GeminiQueryEmbedder(gemini_client), query_embedding_store)
    require_searchable_version(repository_record, arguments.repository, arguments.version, embedder)
    chunks_collection = database[CHUNKS_COLLECTION]
    require_queryable_index(chunks_collection, VECTOR_INDEX_NAME)
    require_queryable_index(chunks_collection, KEYWORD_INDEX_NAME)
    if arguments.no_router:
        route = hybrid_without_router(arguments.question)
    else:
        route = route_query(arguments.question)
    timings: dict[str, float] = {}
    scorer = None
    if route.route == QueryRoute.HYBRID or arguments.expand:
        stage_started = time.perf_counter()
        scorer = CrossEncoderScorer(verified_reranker_files())
        timings["load reranker"] = _milliseconds_since(stage_started)
    found = find_sources(
        chunks_collection,
        embedder,
        scorer,
        arguments.question,
        arguments.repository,
        arguments.version,
        route,
        exclude_tests=arguments.exclude_tests,
    )
    if not found.sources:
        raise _NoSourcesFound()
    timings.update(found.timings)
    sources = found.sources
    related_notes: list[str | None] | None = None
    expansion = None
    if arguments.expand and scorer is not None:
        expansion = expand_sources(
            chunks_collection,
            scorer,
            arguments.question,
            found.sources,
            arguments.repository,
            arguments.version,
        )
        timings.update(expansion.timings)
        sources = expansion.sources
        related_notes = expansion.related_notes
    answer_model = GeminiAnswerModel(
        gemini_client, RateLimiter(ANSWER_REQUESTS_PER_MINUTE, ANSWER_TOKENS_PER_MINUTE)
    )
    stage_started = time.perf_counter()
    generated = generate_answer(arguments.question, sources, answer_model, related_notes)
    timings["generate answer"] = _milliseconds_since(stage_started)
    if embedder.hit_count:
        embedding_status = "question embedding from the cache (0 requests)"
    elif embedder.miss_count:
        embedding_status = "question embedded and cached (1 request)"
    else:
        embedding_status = "question not embedded: keyword search needs none (0 requests)"
    return AskRun(
        generated=generated,
        timings=timings,
        embedding_status=embedding_status,
        expansion=expansion,
        route=found.route,
    )


def _report_lines(
    arguments: argparse.Namespace, repository_record: dict[str, Any], ask_run: AskRun
) -> list[str]:
    generated = ask_run.generated
    commit_id = repository_record["commit_id"]
    license_id = repository_record["license_spdx_id"]
    tests = "test files excluded" if arguments.exclude_tests else "test files included"
    expansion = ask_run.expansion
    searched_count = len(generated.sources)
    if expansion is not None:
        searched_count -= len(expansion.kept_neighbors)
    header_lines = [
        f"Repository  {arguments.repository} at {arguments.version} "
        f"({commit_id[:COMMIT_ID_DISPLAY_LENGTH]}, {license_id})",
        f"Question    {arguments.question}",
        f"Retrieval   {_describe_retrieval(ask_run.route, searched_count)}, {tests}",
        *_expansion_header_lines(expansion, searched_count),
        f"Embedding   {ask_run.embedding_status}",
        f"Model       {GEMINI_ANSWER_MODEL}, low thinking ({_describe_attempts(generated)})",
    ]
    answer_heading = "Answer" if generated.found_answer else "Answer (the code does not show this)"
    answer_lines = _wrapped_answer_lines(generated.answer)
    reply = generated.reply
    token_lines = [
        _row("prompt", _describe_token_count(reply.prompt_tokens)),
        _row("thinking", _describe_token_count(reply.thinking_tokens)),
        _row("answer", _describe_token_count(reply.output_tokens)),
    ]
    timing_lines = [
        _row(stage, f"{milliseconds:.0f} ms") for stage, milliseconds in ask_run.timings.items()
    ]
    return [
        *header_lines,
        "",
        answer_heading,
        *answer_lines,
        "",
        *_citation_lines(generated, arguments.repository, commit_id),
        f"License     {license_id}",
        "",
        "Tokens",
        *token_lines,
        "",
        "Timing",
        *timing_lines,
    ]


def _describe_retrieval(route: RouteDecision, searched_count: int) -> str:
    if route.route == QueryRoute.KEYWORD:
        return f"keyword search for {route.query} ({route.reason}), top {searched_count} sources"
    return (
        f"hybrid search ({route.reason}), top {RERANK_CANDIDATE_COUNT} reranked to "
        f"{searched_count} sources"
    )


def _expansion_header_lines(expansion: ExpandedSources | None, searched_count: int) -> list[str]:
    if expansion is None:
        return []
    lines = [
        f"Expansion   {expansion.found_neighbor_count} callers and callees found, "
        f"best {len(expansion.kept_neighbors)} added by the reranker"
    ]
    for number, kept in enumerate(expansion.kept_neighbors, start=searched_count + 1):
        result = kept.neighbor.result
        lines.append(
            f"            [{number}] {result.kind} {result.qualified_name}, {kept.note} "
            f"(score {kept.rerank_score:.2f})"
        )
    return lines


def _citation_lines(generated: GeneratedAnswer, repository: str, commit_id: str) -> list[str]:
    if not generated.citations:
        return []
    lines = ["Cited code"]
    for citation in _unique_citations(generated.citations):
        source = generated.sources[citation.source_number - 1]
        link = GITHUB_LINE_LINK.format(
            repository=repository,
            commit_id=commit_id,
            file_path=source.file_path,
            start=citation.start_line,
            end=citation.end_line,
        )
        lines.append(
            f"  [{citation.source_number}] {source.file_path}:"
            f"{citation.start_line}-{citation.end_line}  {source.kind} {source.qualified_name}"
        )
        lines.append(f"      {link}")
        lines.extend(f"      {line}" for line in _snippet_lines(source, citation))
        lines.append("")
    return lines


def _wrapped_answer_lines(answer: str) -> list[str]:
    """Wrap each line of the answer on its own, so numbered steps keep their own lines."""
    wrapped_lines: list[str] = []
    for answer_line in answer.splitlines():
        if not answer_line.strip():
            wrapped_lines.append("")
            continue
        wrapped_lines.extend(
            textwrap.wrap(
                answer_line,
                width=ANSWER_WRAP_WIDTH,
                initial_indent="  ",
                subsequent_indent="  ",
            )
        )
    return wrapped_lines


def _describe_token_count(token_count: int | None) -> str:
    return "not reported" if token_count is None else str(token_count)


def _describe_attempts(generated: GeneratedAnswer) -> str:
    attempt_count = generated.reply.attempt_count
    if attempt_count == 1:
        return "1 request"
    return f"{attempt_count} requests: {attempt_count - 1} retried after a temporary failure"


def _unique_citations(citations: list[Citation]) -> list[Citation]:
    return list(dict.fromkeys(citations))


def _snippet_lines(source: SearchResult, citation: Citation) -> list[str]:
    """Show at most `MAX_SNIPPET_LINES` cited lines; outlines show their opening lines instead."""
    code_lines = source_code_lines(source)
    if is_outline(source):
        shown_lines = code_lines[:MAX_SNIPPET_LINES]
        remaining_count = len(code_lines) - len(shown_lines)
        snippet = ["(class outline: method bodies hidden)", *shown_lines]
    else:
        first_index = citation.start_line - source.start_line
        last_index = citation.end_line - source.start_line
        cited_lines = code_lines[first_index : last_index + 1]
        shown_lines = cited_lines[:MAX_SNIPPET_LINES]
        remaining_count = len(cited_lines) - len(shown_lines)
        snippet = [
            f"{line_number}| {line}"
            for line_number, line in enumerate(shown_lines, start=citation.start_line)
        ]
    if remaining_count > 0:
        snippet.append(f"... {remaining_count} more lines at the link")
    return snippet


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", type=_non_blank_question, help="a plain-English question")
    parser.add_argument("--repository", required=True, help="<owner>/<repo>, as indexed")
    parser.add_argument("--version", required=True, help="the indexed tag, branch, or commit ID")
    parser.add_argument(
        "--exclude-tests", action="store_true", help="leave out chunks from test files"
    )
    parser.add_argument(
        "--no-router",
        action="store_true",
        help="send every question to hybrid search, even a single code name",
    )
    parser.add_argument(
        "--expand",
        action="store_true",
        help="also add the best 3 callers or callees of the sources, from the call graph",
    )
    return parser.parse_args()


def _non_blank_question(value: str) -> str:
    question = value.strip()
    if not question:
        raise argparse.ArgumentTypeError("the question must not be blank")
    return question


def _row(label: str, value: object) -> str:
    return f"  {label.ljust(LABEL_WIDTH)}{value}"


def _milliseconds_since(started: float) -> float:
    return (time.perf_counter() - started) * MILLISECONDS_PER_SECOND


def _print(line: str) -> None:
    print(redact(line))


if __name__ == "__main__":
    sys.exit(main())
