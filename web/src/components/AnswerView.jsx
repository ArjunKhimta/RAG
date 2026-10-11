import {
  citationTitle,
  lineRangeText,
  snippetAnchor,
  snippetIndexFor,
  sourceByNumber,
} from "../answers/citations.js";
import { safeGitHubLink } from "../links.js";
import CodeSnippet from "./CodeSnippet.jsx";
import LicenseNotice from "./LicenseNotice.jsx";

export default function AnswerView({ messageId, reply }) {
  return (
    <div className="answer">
      {reply.found_answer ? (
        <AnswerSentences messageId={messageId} reply={reply} />
      ) : (
        <p className="answer-not-found">{reply.answer}</p>
      )}
      {reply.snippets.length > 0 && (
        <div className="answer-snippets">
          {reply.snippets.map((snippet, index) => (
            <CodeSnippet
              key={index}
              anchor={snippetAnchor(messageId, index)}
              snippet={snippet}
              link={snippet.link}
              heading={snippetHeading(snippet, reply.sources)}
            />
          ))}
        </div>
      )}
      {reply.sources.length > 0 && <SourceList sources={reply.sources} />}
      <LicenseNotice repository={reply.repository} />
    </div>
  );
}

function AnswerSentences({ messageId, reply }) {
  return (
    <p className="answer-text">
      {reply.sentences.map((sentence, sentenceIndex) => (
        <span key={sentenceIndex}>
          {sentence.text}
          {sentence.citations.map((citation, citationIndex) => (
            <CitationMarker
              key={citationIndex}
              messageId={messageId}
              citation={citation}
              reply={reply}
            />
          ))}{" "}
        </span>
      ))}
    </p>
  );
}

function CitationMarker({ messageId, citation, reply }) {
  const snippetIndex = snippetIndexFor(citation, reply.snippets);
  const label = `[${citation.source}]`;
  const title = citationTitle(citation, reply.sources);

  if (snippetIndex === -1) {
    return (
      <sup className="citation" title={title}>
        {label}
      </sup>
    );
  }
  return (
    <sup className="citation">
      <a href={`#${snippetAnchor(messageId, snippetIndex)}`} title={title}>
        {label}
      </a>
    </sup>
  );
}

function snippetHeading(snippet, sources) {
  const source = sourceByNumber(sources, snippet.source);
  const range = lineRangeText(snippet.start_line, snippet.end_line);
  if (source === null) {
    return `[${snippet.source}] ${range}`;
  }
  return `[${snippet.source}] ${source.file_path} · ${source.qualified_name} · ${range}`;
}

function SourceList({ sources }) {
  return (
    <details className="answer-sources">
      <summary>
        {sources.length} {sources.length === 1 ? "source" : "sources"} the answer could draw on
      </summary>
      <ol>
        {sources.map((source) => {
          const link = safeGitHubLink(source.link);
          const text = `${source.file_path} · ${source.qualified_name} · ${lineRangeText(source.start_line, source.end_line)}`;
          return (
            <li key={source.number} value={source.number}>
              {link === null ? (
                text
              ) : (
                <a href={link} target="_blank" rel="noreferrer">
                  {text}
                </a>
              )}
            </li>
          );
        })}
      </ol>
    </details>
  );
}
