export const ANSWER_STAGES = Object.freeze([
  {
    name: "clone",
    when: "When a repository is added",
    detail:
      "A shallow clone of a public repository at the version asked for, refused unless GitHub reports it public, it carries a recognised open-source license, and it fits the size limit. The code is only read, never run.",
    figure: "1.7 s",
    figureLabel: "to clone Flask 3.1.3",
  },
  {
    name: "chunk",
    when: "When a repository is added",
    detail:
      "Tree-sitter parses each Python file into one chunk per function or class, keeping its file, line range, name, and parent class. Secrets in strings and comments are redacted first.",
    figure: "1,009",
    figureLabel: "chunks in Flask 3.1.3",
  },
  {
    name: "embed",
    when: "When a repository is added",
    detail:
      "Each chunk becomes a vector of 768 numbers from gemini-embedding-001, cached by a hash of its content, so unchanged code is never embedded twice.",
    figure: "768",
    figureLabel: "numbers per chunk",
  },
  {
    name: "search",
    when: "For every question",
    detail:
      "The question is embedded, or taken from the cache, and searched against that version's chunks in MongoDB Atlas's vector index. The five closest become the sources.",
    figure: "73 ms",
    figureLabel: "median vector search",
  },
  {
    name: "answer",
    when: "For every question",
    detail:
      "Gemini writes the answer as sentences, each listing the source and lines it rests on. Retrieved code is treated as data, never as instructions.",
    figure: "2.2 s",
    figureLabel: "median for the answer model",
  },
  {
    name: "check",
    when: "For every question",
    detail:
      "A reply is refused if a citation names a source that was not given or lines outside it, or if a found answer cites nothing. A refused reply is never shown.",
    figure: "0 of 200",
    figureLabel: "replies refused in the two latest evaluation runs",
  },
]);

export const DEFAULT_STAGE_INDEX = 4;

export const QUESTION_TIMINGS = Object.freeze({
  measuredIn: "eval/results/2026-10-11-web-measurements.md",
  stages: [
    { name: "checks", milliseconds: 32, className: "stage-checks" },
    { name: "embed question", milliseconds: 37, className: "stage-embed" },
    { name: "vector search", milliseconds: 73, className: "stage-search" },
    { name: "answer model", milliseconds: 2224, className: "stage-answer" },
  ],
});
