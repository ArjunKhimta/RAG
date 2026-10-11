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
