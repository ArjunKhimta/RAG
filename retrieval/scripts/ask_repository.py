"""Answer a question about an indexed repository version, citing exact files and lines.

Run from the repository root with the virtual environment active:

    python retrieval/scripts/ask_repository.py "How does Flask sign the session cookie?" \
        --repository pallets/flask --version 3.1.3

By default every question goes to vector search, whose top 5 are used directly: on the
50-question Flask evaluation it led the other setups in both answer runs. `--search router` uses
the query router instead: a single code name, such as `url_for`, goes to keyword search, whose top
5 are used directly, and anything else goes to hybrid search, whose top 30 are reranked by the
local cross-encoder to the best 5. `--search hybrid` sends every question to hybrid search and the
reranker. Gemini then writes a short answer from the 5 sources with markers such as [2]. Every
reference is checked against the code that was sent; a reply citing anything else is rejected,
never shown. Prints the answer, then for each citation the file, lines, a short snippet, and a
link to those lines on GitHub at the indexed commit, then the license, the tokens used, and the
time for each stage. `--exclude-tests` leaves test files out of the search. `--expand` also adds
the 3 callers or callees of those sources that the cross-encoder rates best for the question,
found through the call graph; it is off by default until measured.

Costs one generation request, plus one embedding request for a question not asked before; a code
name sent to keyword search by the router needs no embedding. A temporary Gemini failure is
retried up to twice, and the report says how many requests were made. Stays under the per-minute
generation limits and stops with a clear message when a daily quota is used up. Run
`download_reranker_model.py` once first if the reranker is needed (`--search router` or `hybrid`,
or `--expand`). Exits 0 when an answer is shown, including one saying the code does not contain
the answer, and 1 on any refusal, rejection, or failure. Every printed line passes through the
redaction module. The steps themselves live in `retrieval.asking`, shared with the retrieval
service; this script parses the arguments and prints the report.
"""

from __future__ import annotations

import argparse
import sys
import textwrap

from pymongo.errors import PyMongoError

from retrieval.answer_generation import AnswerRejectedError, Citation, GeneratedAnswer
from retrieval.answer_sources import ExpandedSources
from retrieval.asking import (
    HYBRID_SEARCH,
    ROUTER_SEARCH,
    SEARCH_CHOICES,
    VECTOR_SEARCH,
    AskRun,
    EmbeddingUse,
    NoSourcesFoundError,
    build_question_asker,
)
from retrieval.clients import build_gemini_client, build_mongo_client
from retrieval.config import (
    GEMINI_ANSWER_MODEL,
    MONGODB_DATABASE,
    RERANK_CANDIDATE_COUNT,
    MissingConfigError,
    load_environment,
)
from retrieval.gemini_errors import GeminiRequestError
from retrieval.query_router import QueryRoute, RouteDecision
from retrieval.redaction import redact
from retrieval.reranker_model import RerankerModelError
from retrieval.reranking import QuestionTooLongError
from retrieval.search_results import SearchRefusedError, SearchResult
from retrieval.snippets import cited_snippet, github_line_link, unique_citations

COMMIT_ID_DISPLAY_LENGTH = 12

ANSWER_WRAP_WIDTH = 96

LABEL_WIDTH = 28


EMBEDDING_STATUS = {
    EmbeddingUse.FROM_CACHE: "question embedding from the cache (0 requests)",
    EmbeddingUse.EMBEDDED: "question embedded and cached (1 request)",
    EmbeddingUse.NOT_NEEDED: "question not embedded: keyword search needs none (0 requests)",
}


def main() -> int:
    arguments = _parse_arguments()
    load_environment()
    try:
        database = build_mongo_client()[MONGODB_DATABASE]
        asker = build_question_asker(database, build_gemini_client())
        ask_run = asker.ask(
            arguments.question,
            arguments.repository,
            arguments.version,
            search=arguments.search,
            exclude_tests=arguments.exclude_tests,
            expand=arguments.expand,
        )
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
    except NoSourcesFoundError:
        _print("No code matched the question, so there is nothing to answer from.")
        return 0
    for line in _report_lines(arguments, ask_run):
        _print(line)
    return 0


def _report_lines(arguments: argparse.Namespace, ask_run: AskRun) -> list[str]:
    generated = ask_run.generated
    commit_id = ask_run.repository_record["commit_id"]
    license_id = ask_run.repository_record["license_spdx_id"]
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
        f"Embedding   {EMBEDDING_STATUS[ask_run.embedding_use]}",
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
    if route.route == QueryRoute.VECTOR:
        return f"vector search ({route.reason}), top {searched_count} sources"
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
    for citation in unique_citations(generated.citations):
        source = generated.sources[citation.source_number - 1]
        link = github_line_link(
            repository, commit_id, source.file_path, citation.start_line, citation.end_line
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


def _snippet_lines(source: SearchResult, citation: Citation) -> list[str]:
    snippet = cited_snippet(source, citation)
    if snippet.is_outline:
        lines = ["(class outline: method bodies hidden)", *(line.text for line in snippet.lines)]
    else:
        lines = [f"{line.number}| {line.text}" for line in snippet.lines]
    if snippet.more_line_count > 0:
        lines.append(f"... {snippet.more_line_count} more lines at the link")
    return lines


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", type=_non_blank_question, help="a plain-English question")
    parser.add_argument("--repository", required=True, help="<owner>/<repo>, as indexed")
    parser.add_argument("--version", required=True, help="the indexed tag, branch, or commit ID")
    parser.add_argument(
        "--exclude-tests", action="store_true", help="leave out chunks from test files"
    )
    parser.add_argument(
        "--search",
        choices=SEARCH_CHOICES,
        default=VECTOR_SEARCH,
        help=(
            f"how to find the sources (default {VECTOR_SEARCH}): {VECTOR_SEARCH} search's top 5; "
            f"the query {ROUTER_SEARCH}, keyword search for a code name and hybrid search with "
            f"reranking otherwise; or {HYBRID_SEARCH} search with reranking for every question"
        ),
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


def _print(line: str) -> None:
    print(redact(line))


if __name__ == "__main__":
    sys.exit(main())
