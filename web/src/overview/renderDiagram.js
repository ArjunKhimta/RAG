let mermaidPromise = null;

let renderCount = 0;

const SYSTEM_FONTS = 'system-ui, -apple-system, "Segoe UI", sans-serif';

function prefersDark() {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

function loadMermaid() {
  if (mermaidPromise === null) {
    mermaidPromise = import("mermaid").then(({ default: mermaid }) => {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        htmlLabels: false,
        flowchart: { htmlLabels: false, useMaxWidth: false },
        theme: prefersDark() ? "dark" : "neutral",
        fontFamily: SYSTEM_FONTS,
      });
      return mermaid;
    });
  }
  return mermaidPromise;
}

export async function renderDiagram(source) {
  const mermaid = await loadMermaid();
  renderCount += 1;
  const { svg } = await mermaid.render(`overview-diagram-${renderCount}`, source);
  return svg;
}
