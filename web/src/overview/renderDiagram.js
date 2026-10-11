import { Timing, timed } from "../timing.js";

let mermaidPromise = null;

let renderCount = 0;

const SYSTEM_FONTS = 'system-ui, -apple-system, "Segoe UI", sans-serif';

export const LAYOUT_ENGINE = "dagre";

function prefersDark() {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
}

function importMermaid() {
  return timed(Timing.LOAD_MERMAID, () => import("mermaid"));
}

function loadMermaid() {
  if (mermaidPromise === null) {
    mermaidPromise = importMermaid().then(({ default: mermaid }) => {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        layout: LAYOUT_ENGINE,
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
  const { svg } = await timed(Timing.DRAW_DIAGRAM, () =>
    mermaid.render(`overview-diagram-${renderCount}`, source),
  );
  return svg;
}
