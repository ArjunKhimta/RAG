import { useEffect, useState } from "react";
import { renderDiagram } from "../overview/renderDiagram.js";
import { releaseImageUrl, svgImageUrl } from "../overview/svgImage.js";

export const DIAGRAM_DESCRIPTION =
  "Diagram of how the most connected files call each other; the table below lists every file.";

export default function OverviewDiagram({ source }) {
  const [imageUrl, setImageUrl] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    let createdUrl = null;
    renderDiagram(source).then(
      (svg) => {
        if (!active) {
          return;
        }
        createdUrl = svgImageUrl(svg);
        setImageUrl(createdUrl);
      },
      () => {
        if (active) {
          setFailed(true);
        }
      },
    );
    return () => {
      active = false;
      if (createdUrl !== null) {
        releaseImageUrl(createdUrl);
      }
    };
  }, [source]);

  return (
    <div className="overview-diagram">
      {failed && (
        <p role="alert">
          The diagram could not be drawn. The table below and the Mermaid source still show every
          file and link.
        </p>
      )}
      {!failed && imageUrl === null && <p className="muted">Drawing the diagram…</p>}
      {imageUrl !== null && <img src={imageUrl} alt={DIAGRAM_DESCRIPTION} />}
      <MermaidSource source={source} />
    </div>
  );
}

function MermaidSource({ source }) {
  const [copied, setCopied] = useState(false);
  const canCopy = typeof navigator !== "undefined" && navigator.clipboard !== undefined;

  async function handleCopy() {
    await navigator.clipboard.writeText(source);
    setCopied(true);
  }

  return (
    <details className="mermaid-source">
      <summary>Mermaid source, to paste into a README or a GitHub comment</summary>
      {canCopy && (
        <button type="button" className="button-plain" onClick={handleCopy}>
          {copied ? "Copied" : "Copy"}
        </button>
      )}
      <pre className="code">
        <code>{source}</code>
      </pre>
    </details>
  );
}
