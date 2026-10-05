"""Hand check of the faithfulness judge: a person labels a sample of the sentences it judged.

The judge is the same model that wrote the answers, and a planted test showed one blind spot: a
claim resting on code no source shows (a setting named from outside knowledge, or what an
unshown function does) was judged supported. The hand check measures how far its verdicts can be
trusted on real answers.

Answers name code in plain text, not in backticks, so names are recognised by their shape: a
word with an underscore (`get_cookie_name`, `SECRET_KEY`), a dot between names of at least two
characters each (`json.loads`, but not "e.g."), or a capital inside the word
(`SecureCookieSession`, `URLSafeTimedSerializer`, but not "URLs"). Plain English words and
all-capital acronyms such as JSON never match. Each judged sentence's names are then sorted:
- in no source: the name appears in no source's text or location, so the claim must come from
  outside knowledge or a guess; this is the blind spot's strong signal
- not defined: the name appears in the sources but none of them defines it, such as a library
  class the code calls; most such mentions are fine, so these are only counted

About 40 sentences are labelled, in three groups chosen with a fixed seed:
- judged not supported: miscited or unsupported verdicts, up to 12; is the judge right when it
  complains?
- names code no source shows: supported verdicts on sentences with a name in no source, up to
  12; how often does the blind spot let one through?
- random supported: the remaining supported sentences, sampled to fill the rest; the fair
  estimate of how often an ordinary supported verdict is right
Groups 1 and 2 are chosen on purpose, so agreement is reported per group, never pooled.

Labelling is blind. Items are shuffled together and numbered, and the worksheet and labels file
show only the answer's sentences and the code, never the verdict, the reason, the group, or the
setup. Those go in a separate key file, read only when scoring. Agreement is reported two ways:
on supported against not supported, which is what the faithfulness score rests on, and on the
exact verdict, with a table of every label against every verdict.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from retrieval.answer_generation import AnswerSentence
from retrieval.search_results import SearchResult

from evaluation.faithfulness_judge import Verdict, cited_lines

HAND_CHECK_SEED = 20261005

TARGET_ITEM_COUNT = 40

GROUP_CAP = 12

MINIMUM_DOTTED_PART_LENGTH = 2

NAME_PATTERN = re.compile(r"(?<![\w.])[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")

INNER_CAPITAL_PATTERNS = (re.compile(r"[a-z][A-Z]"), re.compile(r"[A-Z]{2,}[a-z]{2,}"))

GITHUB_LINE_LINK = "https://github.com/{repository}/blob/{commit}/{file_path}#L{start}-L{end}"

ALLOWED_LABELS = [str(verdict) for verdict in Verdict]


class Group(StrEnum):
    NOT_SUPPORTED = "judged not supported"
    NAME_IN_NO_SOURCE = "names code no source shows"
    RANDOM_SUPPORTED = "random supported"


class LabelsError(ValueError):
    """Raised when a labels file is unfinished or does not match its key."""


@dataclass(frozen=True)
class JudgedSentence:
    question_id: str
    setup: str
    sentence_number: int
    text: str
    verdict: Verdict
    reason: str
    names_in_no_source: list[str]
    names_not_defined: list[str]


@dataclass(frozen=True)
class HandCheckItem:
    item_number: int
    group: Group
    sentence: JudgedSentence


@dataclass(frozen=True)
class GroupAgreement:
    group: Group
    item_count: int
    supported_agreement_count: int
    exact_agreement_count: int


@dataclass(frozen=True)
class Disagreement:
    item_number: int
    group: str
    verdict: str
    label: str
    reason: str
    note: str
    sentence: str


@dataclass(frozen=True)
class HandCheckScore:
    agreements: list[GroupAgreement]
    label_by_verdict: dict[str, dict[str, int]]
    disagreements: list[Disagreement]


def code_like_names(text: str) -> list[str]:
    """Names shaped like code, once each, in the order they first appear."""
    names = [match.group(0) for match in NAME_PATTERN.finditer(text)]
    return list(dict.fromkeys(name for name in names if _is_code_like(name)))


def names_in_no_source(names: list[str], sources: list[SearchResult]) -> list[str]:
    source_texts = [_searchable_text(source) for source in sources]
    return [name for name in names if not _appears_in_any(name, source_texts)]


def names_not_defined(names: list[str], sources: list[SearchResult]) -> list[str]:
    """Names found in the sources' text that no source defines, such as a library class."""
    source_texts = [_searchable_text(source) for source in sources]
    defined_parts = {part for source in sources for part in source.qualified_name.split(".")}
    return [
        name
        for name in names
        if _appears_in_any(name, source_texts) and _last_part(name) not in defined_parts
    ]


def judged_sentences(
    question_id: str,
    setup: str,
    verdict_records: list[dict[str, Any]],
    sentences: list[AnswerSentence],
    sources: list[SearchResult],
) -> list[JudgedSentence]:
    """One entry per judged sentence of one answer, with the names it uses sorted."""
    judged: list[JudgedSentence] = []
    for verdict_record in verdict_records:
        sentence_number = verdict_record["sentence"]
        text = sentences[sentence_number - 1].text
        names = code_like_names(text)
        judged.append(
            JudgedSentence(
                question_id=question_id,
                setup=setup,
                sentence_number=sentence_number,
                text=text,
                verdict=Verdict(verdict_record["verdict"]),
                reason=verdict_record["reason"],
                names_in_no_source=names_in_no_source(names, sources),
                names_not_defined=names_not_defined(names, sources),
            )
        )
    return judged


def group_candidates(sentences: list[JudgedSentence]) -> dict[Group, list[JudgedSentence]]:
    """Every judged sentence in exactly one group, each group in a fixed order."""
    ordered = sorted(
        sentences,
        key=lambda sentence: (sentence.question_id, sentence.setup, sentence.sentence_number),
    )
    not_supported = [sentence for sentence in ordered if sentence.verdict != Verdict.SUPPORTED]
    supported = [sentence for sentence in ordered if sentence.verdict == Verdict.SUPPORTED]
    return {
        Group.NOT_SUPPORTED: not_supported,
        Group.NAME_IN_NO_SOURCE: [
            sentence for sentence in supported if sentence.names_in_no_source
        ],
        Group.RANDOM_SUPPORTED: [
            sentence for sentence in supported if not sentence.names_in_no_source
        ],
    }


def select_items(
    sentences: list[JudgedSentence],
    seed: int = HAND_CHECK_SEED,
    target_count: int = TARGET_ITEM_COUNT,
    group_cap: int = GROUP_CAP,
) -> list[HandCheckItem]:
    """Fill groups 1 and 2 up to the cap, group 3 up to the target, then shuffle and number."""
    generator = random.Random(seed)
    candidates = group_candidates(sentences)
    chosen: list[tuple[Group, JudgedSentence]] = []
    for group in (Group.NOT_SUPPORTED, Group.NAME_IN_NO_SOURCE):
        chosen.extend(_sample(generator, group, candidates[group], group_cap))
    remaining = max(target_count - len(chosen), 0)
    chosen.extend(
        _sample(generator, Group.RANDOM_SUPPORTED, candidates[Group.RANDOM_SUPPORTED], remaining)
    )
    generator.shuffle(chosen)
    return [
        HandCheckItem(item_number=number, group=group, sentence=sentence)
        for number, (group, sentence) in enumerate(chosen, start=1)
    ]


def render_worksheet(
    items: list[HandCheckItem],
    sentences_by_answer: dict[tuple[str, str], list[AnswerSentence]],
    sources_by_answer: dict[tuple[str, str], list[SearchResult]],
    repository: str,
    commit: str,
) -> str:
    """Each item's whole answer with the sentence to label in bold, its cited lines, and links to
    the other sources. Never the verdict, reason, group, or setup."""
    lines = [
        "# Faithfulness hand check worksheet",
        "",
        "For each item, label the bold sentence in the labels file as supported (its cited lines "
        "show everything it says), miscited (they do not, but another source does), or "
        "unsupported (no source shows some part of it). Judge only against the code shown or "
        "linked here, not what you know of Flask. A call shows only that the call is made, not "
        "what the called function does.",
    ]
    for item in items:
        key = (item.sentence.question_id, item.sentence.setup)
        lines.extend(
            _worksheet_item_lines(
                item, sentences_by_answer[key], sources_by_answer[key], repository, commit
            )
        )
    return "\n".join(lines) + "\n"


def labels_document(items: list[HandCheckItem], worksheet_path: str) -> dict[str, Any]:
    return {
        "worksheet": worksheet_path,
        "allowed_labels": ALLOWED_LABELS,
        "items": [
            {"item": item.item_number, "sentence": item.sentence.text, "label": None, "note": ""}
            for item in items
        ],
    }


def key_document(
    items: list[HandCheckItem], sentences: list[JudgedSentence], seed: int
) -> dict[str, Any]:
    candidates = group_candidates(sentences)
    return {
        "seed": seed,
        "target_item_count": TARGET_ITEM_COUNT,
        "group_cap": GROUP_CAP,
        "judged_sentence_count": len(sentences),
        "group_sizes": {str(group): len(members) for group, members in candidates.items()},
        "sentences_with_a_name_in_no_source": sum(
            1 for sentence in sentences if sentence.names_in_no_source
        ),
        "sentences_with_a_name_not_defined": sum(
            1 for sentence in sentences if sentence.names_not_defined
        ),
        "items": [
            {
                "item": item.item_number,
                "group": str(item.group),
                "question_id": item.sentence.question_id,
                "setup": item.sentence.setup,
                "sentence": item.sentence.sentence_number,
                "verdict": str(item.sentence.verdict),
                "reason": item.sentence.reason,
                "names_in_no_source": item.sentence.names_in_no_source,
            }
            for item in items
        ],
    }


def score_labels(labels: dict[str, Any], key: dict[str, Any]) -> HandCheckScore:
    """Compare each label with the judge's verdict; refuse unfinished or mismatched labels."""
    labels_by_item = {entry["item"]: entry for entry in labels["items"]}
    keys_by_item = {entry["item"]: entry for entry in key["items"]}
    _require_complete_labels(labels_by_item, keys_by_item)
    agreements = [
        _group_agreement(group, labels_by_item, keys_by_item)
        for group in Group
        if any(entry["group"] == group for entry in keys_by_item.values())
    ]
    label_by_verdict = {
        label: {verdict: 0 for verdict in ALLOWED_LABELS} for label in ALLOWED_LABELS
    }
    disagreements: list[Disagreement] = []
    for item_number in sorted(keys_by_item):
        key_entry = keys_by_item[item_number]
        label_entry = labels_by_item[item_number]
        label_by_verdict[label_entry["label"]][key_entry["verdict"]] += 1
        if label_entry["label"] != key_entry["verdict"]:
            disagreements.append(
                Disagreement(
                    item_number=item_number,
                    group=key_entry["group"],
                    verdict=key_entry["verdict"],
                    label=label_entry["label"],
                    reason=key_entry["reason"],
                    note=label_entry.get("note", ""),
                    sentence=label_entry["sentence"],
                )
            )
    return HandCheckScore(
        agreements=agreements,
        label_by_verdict=label_by_verdict,
        disagreements=disagreements,
    )


def _is_code_like(name: str) -> bool:
    if "." in name:
        return all(len(part) >= MINIMUM_DOTTED_PART_LENGTH for part in name.split("."))
    if "_" in name:
        return True
    return any(pattern.search(name) for pattern in INNER_CAPITAL_PATTERNS)


def _searchable_text(source: SearchResult) -> str:
    return "\n".join([source.text, source.qualified_name, source.file_path])


def _appears_in_any(name: str, texts: Iterable[str]) -> bool:
    whole_word = re.compile(rf"(?<![\w]){re.escape(_last_part(name))}(?![\w])")
    return any(whole_word.search(text) for text in texts)


def _last_part(name: str) -> str:
    return name.split(".")[-1]


def _sample(
    generator: random.Random, group: Group, candidates: list[JudgedSentence], count: int
) -> list[tuple[Group, JudgedSentence]]:
    picked = generator.sample(candidates, min(count, len(candidates)))
    return [(group, sentence) for sentence in picked]


def _worksheet_item_lines(
    item: HandCheckItem,
    sentences: list[AnswerSentence],
    sources: list[SearchResult],
    repository: str,
    commit: str,
) -> list[str]:
    target_number = item.sentence.sentence_number
    answer_parts = []
    for number, sentence in enumerate(sentences, start=1):
        text = sentence.text.strip()
        answer_parts.append(f"**{text}**" if number == target_number else text)
    lines = ["", f"## Item {item.item_number}", "", "Answer: " + " ".join(answer_parts), ""]
    for citation in sentences[target_number - 1].citations:
        heading, *code_lines = cited_lines(citation, sources)
        source = sources[citation.source_number - 1]
        link = _github_link(
            repository, commit, source.file_path, citation.start_line, citation.end_line
        )
        lines.extend([f"{heading} `{source.file_path}` ([GitHub]({link}))", "", "~~~~"])
        lines.extend(code_lines)
        lines.extend(["~~~~", ""])
    lines.append("All sources:")
    for number, source in enumerate(sources, start=1):
        link = _github_link(
            repository, commit, source.file_path, source.start_line, source.end_line
        )
        lines.append(
            f"- Source {number}: {source.kind} `{source.qualified_name}`, `{source.file_path}` "
            f"lines {source.start_line}-{source.end_line} ([GitHub]({link}))"
        )
    return lines


def _github_link(
    repository: str, commit: str, file_path: str, start_line: int, end_line: int
) -> str:
    return GITHUB_LINE_LINK.format(
        repository=repository, commit=commit, file_path=file_path, start=start_line, end=end_line
    )


def _require_complete_labels(
    labels_by_item: dict[int, dict[str, Any]], keys_by_item: dict[int, dict[str, Any]]
) -> None:
    if set(labels_by_item) != set(keys_by_item):
        raise LabelsError("The labels file and its key list different items")
    unfinished = [
        item_number
        for item_number in sorted(labels_by_item)
        if labels_by_item[item_number]["label"] not in ALLOWED_LABELS
    ]
    if unfinished:
        listed = ", ".join(str(item_number) for item_number in unfinished)
        raise LabelsError(f"Items without a label of {', '.join(ALLOWED_LABELS)}: {listed}")


def _group_agreement(
    group: Group,
    labels_by_item: dict[int, dict[str, Any]],
    keys_by_item: dict[int, dict[str, Any]],
) -> GroupAgreement:
    item_numbers = [number for number, entry in keys_by_item.items() if entry["group"] == group]
    pairs = [
        (labels_by_item[number]["label"], keys_by_item[number]["verdict"])
        for number in item_numbers
    ]
    supported = str(Verdict.SUPPORTED)
    return GroupAgreement(
        group=group,
        item_count=len(pairs),
        supported_agreement_count=sum(
            1 for label, verdict in pairs if (label == supported) == (verdict == supported)
        ),
        exact_agreement_count=sum(1 for label, verdict in pairs if label == verdict),
    )
