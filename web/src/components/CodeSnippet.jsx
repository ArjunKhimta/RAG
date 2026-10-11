import { safeGitHubLink } from "../links.js";

export default function CodeSnippet({ snippet, link, heading, anchor }) {
  const safeLink = safeGitHubLink(link);

  return (
    <figure className="code-snippet" id={anchor}>
      <figcaption>
        {safeLink === null ? (
          heading
        ) : (
          <a href={safeLink} target="_blank" rel="noreferrer">
            {heading}
          </a>
        )}
        {snippet.is_outline && <span className="muted"> · class outline, method bodies left out</span>}
      </figcaption>
      <pre className="code">
        <code>
          {snippet.lines.map((line, index) => (
            <span className="code-line" key={index}>
              <span className="line-number">{line.number ?? ""}</span>
              {line.text}
              {"\n"}
            </span>
          ))}
        </code>
      </pre>
      {snippet.more_lines > 0 && (
        <p className="muted snippet-more">
          {snippet.more_lines} more {snippet.more_lines === 1 ? "line" : "lines"}
          {safeLink !== null && (
            <>
              {" "}
              <a href={safeLink} target="_blank" rel="noreferrer">
                on GitHub
              </a>
            </>
          )}
        </p>
      )}
    </figure>
  );
}
