export function snippetAnchor(messageId, snippetIndex) {
  return `snippet-${messageId}-${snippetIndex}`;
}

export function snippetIndexFor(citation, snippets) {
  return snippets.findIndex(
    (snippet) =>
      snippet.source === citation.source &&
      snippet.start_line === citation.start_line &&
      snippet.end_line === citation.end_line,
  );
}

export function sourceByNumber(sources, number) {
  return sources.find((source) => source.number === number) ?? null;
}

export function lineRangeText(startLine, endLine) {
  return startLine === endLine ? `line ${startLine}` : `lines ${startLine}–${endLine}`;
}

export function citationTitle(citation, sources) {
  const source = sourceByNumber(sources, citation.source);
  const range = lineRangeText(citation.start_line, citation.end_line);
  return source === null ? range : `${source.file_path}, ${range}`;
}

const SHORT_COMMIT_LENGTH = 7;

export function shortCommit(commitId) {
  return typeof commitId === "string" ? commitId.slice(0, SHORT_COMMIT_LENGTH) : "";
}
