import { describe, expect, it } from "vitest";
import {
  buildDiagram,
  fileNameOf,
  folderOf,
  mostConnectedFiles,
  safeLabel,
} from "../src/overview/diagram.js";
import { sizedSvg } from "../src/overview/svgImage.js";
import { flaskOverview, overviewFile } from "./support/overviews.js";

describe("diagram labels", () => {
  it("keeps ordinary path characters", () => {
    expect(safeLabel("src/flask/json/provider_2.py")).toBe("src/flask/json/provider_2.py");
  });

  it("replaces anything Mermaid could read as syntax or markup", () => {
    expect(safeLabel('a"]-->evil["b')).toBe("a__--_evil__b");
    expect(safeLabel("<img src=x>")).toBe("_img src_x_");
    expect(safeLabel("%%{init}%%")).toBe("___init___");
  });

  it("splits a path into its folder and file name", () => {
    expect(folderOf("src/flask/app.py")).toBe("src/flask");
    expect(folderOf("setup.py")).toBe("");
    expect(fileNameOf("src/flask/app.py")).toBe("app.py");
  });
});

describe("choosing files", () => {
  it("keeps the most connected files, breaking ties by path, and drops unconnected ones", () => {
    const files = [
      overviewFile("b.py", { calls_in: 2 }),
      overviewFile("a.py", { calls_out: 2 }),
      overviewFile("c.py", { calls_in: 5 }),
      overviewFile("lonely.py"),
    ];

    expect(mostConnectedFiles(files, 2).map((file) => file.path)).toEqual(["c.py", "a.py"]);
    expect(mostConnectedFiles(files).map((file) => file.path)).toEqual(["c.py", "a.py", "b.py"]);
  });
});

describe("buildDiagram", () => {
  it("writes a flowchart with folders as groups and call counts on the arrows", () => {
    const diagram = buildDiagram(flaskOverview());

    expect(diagram.source).toBe(
      [
        "flowchart LR",
        '  subgraph g0["src/flask"]',
        '    f0["helpers.py"]',
        '    f1["app.py"]',
        '    f2["sessions.py"]',
        "  end",
        "  f1 -->|9| f0",
        "  f0 -->|2| f1",
        "  f2 -->|2| f0",
      ].join("\n"),
    );
    expect(diagram.shownFileCount).toBe(3);
    expect(diagram.shownLinkCount).toBe(3);
    expect(diagram.connectedFileCount).toBe(3);
  });

  it("leaves out links to files that did not make the cut", () => {
    const diagram = buildDiagram(flaskOverview(), 2);

    expect(diagram.source).not.toContain("sessions.py");
    expect(diagram.shownLinkCount).toBe(2);
    expect(diagram.connectedFileCount).toBe(3);
  });

  it("puts files at the repository root outside any group", () => {
    const diagram = buildDiagram({
      files: [overviewFile("setup.py", { calls_out: 1 }), overviewFile("pkg/core.py", { calls_in: 1 })],
      links: [{ from: "setup.py", to: "pkg/core.py", calls: 1 }],
    });

    expect(diagram.source.split("\n")).toEqual([
      "flowchart LR",
      '  f1["setup.py"]',
      '  subgraph g1["pkg"]',
      '    f0["core.py"]',
      "  end",
      "  f1 -->|1| f0",
    ]);
  });

  it("never copies a file name into the diagram unescaped", () => {
    const diagram = buildDiagram({
      files: [
        overviewFile('evil"]:::x/a.py', { calls_out: 1 }),
        overviewFile("b.py", { calls_in: 1 }),
      ],
      links: [{ from: 'evil"]:::x/a.py', to: "b.py", calls: 1 }],
    });

    expect(diagram.source).not.toContain('"]:::');
    expect(diagram.source).toContain('subgraph g1["evil_____x"]');
  });

  it("has nothing to draw when no file calls another", () => {
    const diagram = buildDiagram({ files: [overviewFile("a.py")], links: [] });

    expect(diagram.shownLinkCount).toBe(0);
    expect(diagram.shownFileCount).toBe(0);
  });
});

describe("sizedSvg", () => {
  it("sizes the drawing from its view box and drops its inline style", () => {
    const svg =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640.5 220" style="max-width: 640px"><g/></svg>';

    const sized = new DOMParser().parseFromString(sizedSvg(svg), "image/svg+xml").documentElement;

    expect(sized.getAttribute("width")).toBe("641");
    expect(sized.getAttribute("height")).toBe("220");
    expect(sized.hasAttribute("style")).toBe(false);
  });

  it("leaves the size alone when there is no usable view box", () => {
    const sized = sizedSvg('<svg xmlns="http://www.w3.org/2000/svg"><g/></svg>');

    expect(sized).not.toContain("width=");
  });
});
