# Faithfulness hand check

**Labelled by Claude (claude-opus-5-5), not by a person.** Arjun asked Claude to label instead of doing the hand check, so "hand label" below means Claude's label: a check of one AI model's judge by another, labelled blind to the key. No human check of the judge has been done yet; the blank `2026-10-06-1107-handcheck-labels.json` is kept for one.

- Judge verdicts: `eval/results/2026-10-06-1101-faithfulness.json`
- Labels: `eval/results/2026-10-06-1107-claude-handcheck-labels.json`, seed 20261005
- Judged sentences: 230; with a name that appears in no source: 0; with a name the sources use but do not define: 65
- Group sizes before sampling: judged not supported 2, names code no source shows 0, random supported 228

## Agreement by group

| Group | Items | Supported or not agrees | Exact verdict agrees |
| --- | --- | --- | --- |
| judged not supported | 2 | 2/2 (100%) | 1/2 (50%) |
| random supported | 38 | 36/38 (95%) | 36/38 (95%) |

## Hand label (rows) against judge verdict (columns), all items

| Hand label | supported | miscited | unsupported |
| --- | --- | --- | --- |
| supported | 36 | 0 | 0 |
| miscited | 2 | 1 | 1 |
| unsupported | 0 | 0 | 0 |

## Disagreements

- Item 24 (random supported): judge supported, hand miscited. "Teardown functions are called after the request is dispatched and the response is returned, right before the request context is popped." Judge's reason: Cited lines 1330-1331 directly state that teardown functions are called after the request is dispatched and before the request context is popped. Note: The cited lines say when do_teardown_request is called; that it calls the teardown functions is on uncited lines 1333-1334.
- Item 36 (random supported): judge supported, hand miscited. "At the end of each request, process_response calls the save_session method of the session interface." Judge's reason: Cited lines 1321-1322 clearly show save_session being called if not a null session. Note: The call is shown (only when the session is not null); 'at the end of each request' rests on process_response's uncited docstring.
- Item 37 (judged not supported): judge unsupported, hand miscited. "You can disable this by setting app.json.sort_keys to False." Judge's reason: Cited lines assert sort_keys is True, but do not show disabling it. Note: The cited test only asserts sort_keys is on by default; that it can be disabled is in source 4 (DefaultJSONProvider.sort_keys docstring).

Supported or not agrees: the hand label and the judge agree on whether the sentence is supported, which is what the faithfulness score rests on. Exact verdict agrees: they also agree on miscited against unsupported. Labelled blind: the worksheet showed neither the verdict nor the group. Groups 1 and 2 are chosen on purpose, so their figures are not estimates for all sentences; the random supported group is. One labeller.
