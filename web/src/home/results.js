export const SearchSetup = Object.freeze({
  VECTOR: "vector",
  EXPANDED: "vector + expand",
});

export const MEASURED_RESULTS = Object.freeze({
  measuredIn: [
    "eval/results/2026-10-06-1054-answers.md",
    "eval/results/2026-10-06-1101-faithfulness.md",
  ],
  setups: {
    [SearchSetup.VECTOR]: {
      label: "Vector search (live)",
      note: "One run on 50 hand-checked questions about Flask 3.1.3. Sentences were judged by Gemini and have not yet been checked by a person. This is the setup the site uses.",
      figures: [
        { label: "Answers citing every expected definition", value: "28 / 47" },
        { label: "Answers citing at least one", value: "41 / 47" },
        { label: "Questions with no answer in the code, refused", value: "3 / 3" },
        { label: "Answer sentences shown by their cited lines", value: "113 / 114" },
        { label: "Median prompt tokens", value: "2,137" },
      ],
    },
    [SearchSetup.EXPANDED]: {
      label: "+ callers and callees",
      note: "Adding the callers and callees of each source gave no gain in complete answers (27 against 28 in this run) and cost about 840 more prompt tokens per question, so the site leaves it off.",
      figures: [
        { label: "Answers citing every expected definition", value: "27 / 47" },
        { label: "Answers citing at least one", value: "42 / 47" },
        { label: "Questions with no answer in the code, refused", value: "3 / 3" },
        { label: "Answer sentences shown by their cited lines", value: "115 / 116" },
        { label: "Median prompt tokens", value: "2,977" },
      ],
    },
  },
});
