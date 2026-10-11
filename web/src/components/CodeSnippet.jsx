import { safeGitHubLink } from "../links.js";
import HighlightedCode from "./HighlightedCode.jsx";

export default function CodeSnippet({ snippet, link, heading, anchor, hidden = false }) {
  const safeLink = safeGitHubLink(link);

  return (
    <figure className="code-snippet" id={anchor} hidden={hidden}>
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
      <HighlightedCode lines={snippet.lines} />
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
