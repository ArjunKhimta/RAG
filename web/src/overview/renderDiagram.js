import { Timing, timed } from "../timing.js";

let mermaidPromise = null;

let renderCount = 0;

const DIAGRAM_FONTS = 'ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace';

export const LAYOUT_ENGINE = "dagre";

const DARK_DIAGRAM_COLOURS = Object.freeze({
  background: "#0f1318",
  primaryColor: "#161c24",
  primaryTextColor: "#e6ebf1",
  primaryBorderColor: "#2c3440",
  lineColor: "#f0b44c",
  textColor: "#e6ebf1",
  clusterBkg: "#0c0f13",
  clusterBorder: "#2c3440",
  edgeLabelBackground: "#0f1318",
});

const LIGHT_DIAGRAM_COLOURS = Object.freeze({
  background: "#ffffff",
  primaryColor: "#eef1f5",
  primaryTextColor: "#12161c",
  primaryBorderColor: "#c3cad4",
  lineColor: "#8a5300",
  textColor: "#12161c",
  clusterBkg: "#f7f8fa",
  clusterBorder: "#c3cad4",
  edgeLabelBackground: "#ffffff",
});

function prefersLight() {
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ?? false;
}

export function diagramColours() {
  return prefersLight() ? LIGHT_DIAGRAM_COLOURS : DARK_DIAGRAM_COLOURS;
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
        theme: "base",
        themeVariables: { ...diagramColours(), fontFamily: DIAGRAM_FONTS },
        fontFamily: DIAGRAM_FONTS,
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
