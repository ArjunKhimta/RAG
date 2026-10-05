"""What the faithfulness judge reads: the answered replies of a saved answer run, and their sources.

An answer run saves each source as a location with its chunk ID, never its code. The judge needs
the exact text the answer model saw, so the sources are fetched again from the `chunks`
collection by chunk ID. A chunk ID names the repository, version, file, first line, kind, and
part, but not the commit, so every fetched chunk must also match its saved location and the
answer run's indexed commit; anything else means the index changed since the answers were
written, and the judge refuses rather than check sentences against different code.

Only answered replies are judged. A reply that said the answer was not found makes no claim
about the code, and a rejected or failed reply was never shown to anyone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pymongo.database import Database
from retrieval.answer_generation import AnswerSentence, Citation
from retrieval.chunk_store import CHUNKS_COLLECTION, MongoChunkStore
from retrieval.search_results import (
    RESULT_FIELDS,
    SearchRefusedError,
    SearchResult,
    require_indexed_version,
    search_result_from,
)

from evaluation.answer_metrics import AnswerOutcome
from evaluation.faithfulness_judge import cited_lines

UNUSED_SCORE = 0.0

LOCATION_FIELDS = ("file_path", "qualified_name", "kind", "start_line", "end_line")

CHUNK_PROJECTION: dict[str, int] = {
    **{field_name: 1 for field_name in RESULT_FIELDS},
    "commit_id": 1,
}


class AnswerRunFormatError(ValueError):
    """Raised when a saved answer run lacks what the judge needs, such as per-sentence citations."""


class ChangedSourceError(ValueError):
    """Raised when a saved source no longer matches the chunk stored under its ID."""


@dataclass(frozen=True)
class AnswerToJudge:
    question_id: str
    setup: str
    sentences: list[AnswerSentence]
    source_locations: list[dict[str, Any]]


def answers_to_judge(answer_run: dict[str, Any]) -> list[AnswerToJudge]:
    """Every answered reply, in question order and then setup order, as the run saved them."""
    answers: list[AnswerToJudge] = []
    for question in answer_run["questions"]:
        for setup, record in question["setups"].items():
            if record["outcome"] != AnswerOutcome.ANSWERED:
                continue
            _require_judgeable(question["id"], setup, record)
            answers.append(
                AnswerToJudge(
                    question_id=question["id"],
                    setup=setup,
                    sentences=[_sentence_from(sentence) for sentence in record["sentences"]],
                    source_locations=record["sources"],
                )
            )
    return answers


def chunk_ids_of(answers: list[AnswerToJudge]) -> list[str]:
    """Each chunk ID the answers' sources need, once, in first-seen order."""
    chunk_ids = [location["chunk_id"] for answer in answers for location in answer.source_locations]
    return list(dict.fromkeys(chunk_ids))


def sources_for(
    answer: AnswerToJudge, chunks_by_id: dict[str, dict[str, Any]], indexed_commit: str
) -> list[SearchResult]:
    """Rebuild the answer's sources, in their original order, from the stored chunks.

    Search scores are not saved and the judge does not use them, so every source gets the same
    unused score. The answer's citations are checked against the rebuilt sources here, so a bad
    saved answer stops the run before any request is made.
    """
    sources = [
        _source_from(location, chunks_by_id, indexed_commit) for location in answer.source_locations
    ]
    _require_citations_fit(answer, sources)
    return sources


def fetch_answer_sources(
    database: Database, answers_run_details: dict[str, Any], answers: list[AnswerToJudge]
) -> list[list[SearchResult]]:
    """Fetch and check every answer's sources, so a changed index stops a run before it starts."""
    repository = answers_run_details["repository"]
    version = answers_run_details["version"]
    indexed_commit = answers_run_details["indexed_commit"]
    record = MongoChunkStore(database).find_repository_record(repository, version)
    require_indexed_version(record, repository, version)
    if record["commit_id"] != indexed_commit:
        raise SearchRefusedError(
            f"The answers were written from commit {indexed_commit}, but the index is at "
            f"{record['commit_id']}"
        )
    chunk_filter = {
        "_id": {"$in": chunk_ids_of(answers)},
        "repository": repository,
        "version": version,
    }
    chunks_by_id = {
        document["_id"]: document
        for document in database[CHUNKS_COLLECTION].find(chunk_filter, CHUNK_PROJECTION)
    }
    return [sources_for(answer, chunks_by_id, indexed_commit) for answer in answers]


def _require_judgeable(question_id: str, setup: str, record: dict[str, Any]) -> None:
    has_sentences = "sentences" in record
    has_chunk_ids = all("chunk_id" in location for location in record["sources"])
    if not has_sentences or not has_chunk_ids:
        raise AnswerRunFormatError(
            f"The answer to {question_id} from {setup} was saved without per-sentence citations "
            "or source chunk IDs; run a new answer evaluation to judge faithfulness"
        )


def _require_citations_fit(answer: AnswerToJudge, sources: list[SearchResult]) -> None:
    citations = [citation for sentence in answer.sentences for citation in sentence.citations]
    if not citations:
        raise AnswerRunFormatError(
            f"The answer to {answer.question_id} from {answer.setup} is marked answered but "
            "cites nothing"
        )
    for citation in citations:
        try:
            cited_lines(citation, sources)
        except ValueError as error:
            raise AnswerRunFormatError(
                f"The answer to {answer.question_id} from {answer.setup}: {error}"
            ) from error


def _sentence_from(sentence_record: dict[str, Any]) -> AnswerSentence:
    citations = [
        Citation(
            source_number=citation["source_number"],
            start_line=citation["start_line"],
            end_line=citation["end_line"],
        )
        for citation in sentence_record["citations"]
    ]
    return AnswerSentence(text=sentence_record["text"], citations=citations)


def _source_from(
    location: dict[str, Any], chunks_by_id: dict[str, dict[str, Any]], indexed_commit: str
) -> SearchResult:
    chunk_id = location["chunk_id"]
    document = chunks_by_id.get(chunk_id)
    if document is None:
        raise ChangedSourceError(f"No stored chunk has the saved ID {chunk_id}")
    if document.get("commit_id") != indexed_commit:
        raise ChangedSourceError(
            f"The chunk {chunk_id} is from commit {document.get('commit_id')}, but the answers "
            f"were written from commit {indexed_commit}"
        )
    for field_name in LOCATION_FIELDS:
        if document[field_name] != location[field_name]:
            raise ChangedSourceError(
                f"The chunk {chunk_id} has {field_name} {document[field_name]!r}, but the answer "
                f"run saved {location[field_name]!r}"
            )
    return search_result_from({**document, "_id": chunk_id, "score": UNUSED_SCORE})
