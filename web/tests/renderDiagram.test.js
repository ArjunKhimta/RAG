import { beforeEach, describe, expect, it, vi } from "vitest";

const fakeMermaid = vi.hoisted(() => ({
  initialize: vi.fn(),
  render: vi.fn(async () => ({ svg: "<svg/>" })),
}));

vi.mock("mermaid", () => ({ default: fakeMermaid }));

async function freshRenderModule() {
  vi.resetModules();
  return import("../src/overview/renderDiagram.js");
}

beforeEach(() => {
  fakeMermaid.initialize.mockClear();
  fakeMermaid.render.mockClear();
});

describe("drawing a diagram with Mermaid", () => {
  it("lays it out with dagre, so the large ELK engine is never downloaded", async () => {
    const { LAYOUT_ENGINE, renderDiagram } = await freshRenderModule();

    await renderDiagram("flowchart LR\n  f0 --> f1");

    expect(LAYOUT_ENGINE).toBe("dagre");
    expect(fakeMermaid.initialize).toHaveBeenCalledWith(
      expect.objectContaining({ layout: "dagre", securityLevel: "strict", htmlLabels: false }),
    );
  });

  it("draws in the site's dark colours unless the visitor asks for light", async () => {
    const { renderDiagram } = await freshRenderModule();

    await renderDiagram("flowchart LR\n  f0 --> f1");

    const settings = fakeMermaid.initialize.mock.calls[0][0];
    expect(settings.theme).toBe("base");
    expect(settings.themeVariables).toMatchObject({ lineColor: "#f0b44c", background: "#0f1318" });
  });

  it("draws in the light colours when the visitor's system asks for light", async () => {
    window.matchMedia = (query) => ({ matches: query === "(prefers-color-scheme: light)" });
    const { renderDiagram } = await freshRenderModule();

    await renderDiagram("flowchart LR\n  f0 --> f1");

    expect(fakeMermaid.initialize.mock.calls[0][0].themeVariables).toMatchObject({
      lineColor: "#8a5300",
      background: "#ffffff",
    });
    delete window.matchMedia;
  });

  it("starts Mermaid once and draws each diagram under a new ID", async () => {
    const { renderDiagram } = await freshRenderModule();

    await renderDiagram("flowchart LR\n  f0 --> f1");
    await renderDiagram("flowchart LR\n  f1 --> f2");

    expect(fakeMermaid.initialize).toHaveBeenCalledTimes(1);
    const ids = fakeMermaid.render.mock.calls.map((call) => call[0]);
    expect(ids).toHaveLength(2);
    expect(new Set(ids).size).toBe(2);
  });
});
