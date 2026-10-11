import { useMemo } from "react";
import { highlightPythonLines } from "../code/pythonHighlighter.js";

export default function HighlightedCode({ lines, citedStart = null, citedEnd = null }) {
  const highlighted = useMemo(() => highlightPythonLines(lines.map((line) => line.text)), [lines]);

  return (
    <pre className="code">
      <code>
        {lines.map((line, index) => (
          <span className={lineClassName(line.number, citedStart, citedEnd)} key={index}>
            <span className="line-number">{line.number ?? ""}</span>
            {highlighted[index].map((token, tokenIndex) => (
              <span className={`token-${token.kind}`} key={tokenIndex}>
                {token.text}
              </span>
            ))}
            {"\n"}
          </span>
        ))}
      </code>
    </pre>
  );
}

function lineClassName(number, citedStart, citedEnd) {
  const cited =
    citedStart !== null && number !== null && number >= citedStart && number <= citedEnd;
  return cited ? "code-line is-cited" : "code-line";
}
