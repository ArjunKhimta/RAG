"""The hand check's agreement table: per group, a label-by-verdict table, and every disagreement.

Groups are never pooled: two of them are chosen on purpose, so a single overall figure would
mislead. Only the random supported group estimates how often an ordinary supported verdict is
right.
"""

from __future__ import annotations

from typing import Any

from evaluation.hand_check import ALLOWED_LABELS, HandCheckScore

PERCENT = 100


def render_hand_check_table(
    score: HandCheckScore, key: dict[str, Any], labels_file: str, faithfulness_file: str
) -> str:
    judged_count = key["judged_sentence_count"]
    lines = [
        "# Faithfulness hand check",
        "",
        f"- Judge verdicts: `{faithfulness_file}`",
        f"- Labels: `{labels_file}`, seed {key['seed']}",
        f"- Judged sentences: {judged_count}; with a name that appears in no source: "
        f"{key['sentences_with_a_name_in_no_source']}; with a name the sources use but do not "
        f"define: {key['sentences_with_a_name_not_defined']}",
        "- Group sizes before sampling: "
        + ", ".join(f"{group} {size}" for group, size in key["group_sizes"].items()),
        "",
        "## Agreement by group",
        "",
        _row(["Group", "Items", "Supported or not agrees", "Exact verdict agrees"]),
        _row(["---"] * 4),
    ]
    for agreement in score.agreements:
        lines.append(
            _row(
                [
                    str(agreement.group),
                    str(agreement.item_count),
                    _count_and_percent(agreement.supported_agreement_count, agreement.item_count),
                    _count_and_percent(agreement.exact_agreement_count, agreement.item_count),
                ]
            )
        )
    lines.extend(
        [
            "",
            "## Hand label (rows) against judge verdict (columns), all items",
            "",
            _row(["Hand label", *ALLOWED_LABELS]),
            _row(["---"] * (len(ALLOWED_LABELS) + 1)),
        ]
    )
    for label in ALLOWED_LABELS:
        counts = score.label_by_verdict[label]
        lines.append(_row([label, *(str(counts[verdict]) for verdict in ALLOWED_LABELS)]))
    lines.extend(["", "## Disagreements", ""])
    if not score.disagreements:
        lines.append("None.")
    for disagreement in score.disagreements:
        note = f" Note: {disagreement.note}" if disagreement.note else ""
        lines.append(
            f"- Item {disagreement.item_number} ({disagreement.group}): judge "
            f'{disagreement.verdict}, hand {disagreement.label}. "{disagreement.sentence}" '
            f"Judge's reason: {disagreement.reason}{note}"
        )
    lines.extend(["", *_measure_notes()])
    return "\n".join(lines) + "\n"


def _measure_notes() -> list[str]:
    return [
        "Supported or not agrees: the hand label and the judge agree on whether the sentence is "
        "supported, which is what the faithfulness score rests on. Exact verdict agrees: they "
        "also agree on miscited against unsupported. Labelled blind: the worksheet showed "
        "neither the verdict nor the group. Groups 1 and 2 are chosen on purpose, so their "
        "figures are not estimates for all sentences; the random supported group is. One "
        "labeller.",
    ]


def _count_and_percent(count: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{count}/{total} ({count / total * PERCENT:.0f}%)"


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"
