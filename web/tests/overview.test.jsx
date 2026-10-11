import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DIAGRAM_DESCRIPTION } from "../src/components/OverviewDiagram.jsx";
import { renderDiagram } from "../src/overview/renderDiagram.js";
import { Timing } from "../src/timing.js";
import { SIGNED_IN } from "./support/accounts.js";
import { installFakeApi, jsonReply } from "./support/fakeApi.js";
import { flaskOverview, overviewFile } from "./support/overviews.js";
import { renderApp } from "./support/renderApp.jsx";
import { indexingLink, readyLink } from "./support/repositories.js";

vi.mock("../src/overview/renderDiagram.js", () => ({ renderDiagram: vi.fn() }));

const DRAWN_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 120"><g/></svg>';

let createdUrls;
let releasedUrls;

beforeEach(() => {
  createdUrls = [];
  releasedUrls = [];
  URL.createObjectURL = vi.fn(() => {
    const url = `blob:diagram-${createdUrls.length + 1}`;
    createdUrls.push(url);
    return url;
  });
  URL.revokeObjectURL = vi.fn((url) => releasedUrls.push(url));
  renderDiagram.mockReset();
  renderDiagram.mockResolvedValue(DRAWN_SVG);
});

afterEach(() => {
  delete URL.createObjectURL;
  delete URL.revokeObjectURL;
});

function overviewApi(overview = flaskOverview(), link = readyLink()) {
  return installFakeApi({
    "GET /api/me": SIGNED_IN,
    "GET /api/repositories/link-flask": jsonReply(200, link),
    "GET /api/repositories/link-flask/overview": jsonReply(200, overview),
  });
}

describe("overview page", () => {
  it("summarizes the files, classes, functions, and linked calls", async () => {
    overviewApi();

    renderApp("/repositories/link-flask/overview");

    expect(await screen.findByRole("heading", { name: "Overview of pallets/flask" })).toBeTruthy();
    expect(
      await screen.findByText(
        /4 source files with 5 classes and 78 functions\. The indexer linked 28 calls between functions: 13\s+between files and 15 within a file\./,
      ),
    ).toBeTruthy();
  });

  it("draws the diagram from the most connected files and shows it as an image", async () => {
    overviewApi();

    renderApp("/repositories/link-flask/overview");

    const image = await screen.findByRole("img", { name: DIAGRAM_DESCRIPTION });
    expect(image.getAttribute("src")).toBe("blob:diagram-1");
    expect(renderDiagram).toHaveBeenCalledTimes(1);
    expect(renderDiagram.mock.calls[0][0]).toContain('f0["helpers.py"]');
    expect(screen.getByText(/The 3 most connected of the 3 files/)).toBeTruthy();
  });

  it("times the diagram until its image has loaded", async () => {
    overviewApi();

    renderApp("/repositories/link-flask/overview");

    const image = await screen.findByRole("img", { name: DIAGRAM_DESCRIPTION });
    expect(performance.getEntriesByName(Timing.SHOWN_DIAGRAM, "measure")).toHaveLength(0);
    fireEvent.load(image);
    expect(performance.getEntriesByName(Timing.SHOWN_DIAGRAM, "measure")).toHaveLength(1);
  });

  it("lets a visitor pick a file and see what it calls and what calls it", async () => {
    overviewApi();

    renderApp("/repositories/link-flask/overview");

    const files = await screen.findByRole("list", {
      name: "Files that call or are called by another file",
    });
    const buttons = within(files).getAllByRole("button");
    expect(buttons.map((button) => button.textContent)).toEqual([
      "src/flask/helpers.py13",
      "src/flask/app.py11",
      "src/flask/sessions.py2",
    ]);
    expect(screen.getByRole("heading", { name: "src/flask/helpers.py", level: 3 })).toBeTruthy();

    await userEvent.click(buttons[1]);

    expect(screen.getByRole("heading", { name: "src/flask/app.py", level: 3 })).toBeTruthy();
    expect(screen.getByText("→ src/flask/helpers.py")).toBeTruthy();
    expect(screen.getByText("← src/flask/helpers.py")).toBeTruthy();
  });

  it("offers the Mermaid source to copy", async () => {
    overviewApi();

    renderApp("/repositories/link-flask/overview");

    const source = await screen.findByText(/Mermaid source, to paste into a README/);
    const details = source.closest("details");
    expect(within(details).getByText(/flowchart LR/).textContent).toContain("f1 -->|9| f0");
  });

  it("explains when the diagram cannot be drawn and keeps the table", async () => {
    renderDiagram.mockRejectedValue(new Error("Parse error"));
    overviewApi();

    renderApp("/repositories/link-flask/overview");

    expect(await screen.findByText(/The diagram could not be drawn/)).toBeTruthy();
    expect(screen.queryByRole("img", { name: DIAGRAM_DESCRIPTION })).toBeNull();
    expect(screen.getByRole("link", { name: "src/flask/app.py" })).toBeTruthy();
    expect(performance.getEntriesByName(Timing.SHOWN_DIAGRAM, "measure")).toHaveLength(0);
    expect(performance.getEntriesByName(`${Timing.SHOWN_DIAGRAM} start`, "mark")).toHaveLength(0);
  });

  it("says so when no file calls another, without drawing", async () => {
    const lonely = {
      ...flaskOverview(),
      files: [overviewFile("src/flask/app.py")],
      links: [],
    };
    overviewApi(lonely);

    renderApp("/repositories/link-flask/overview");

    expect(await screen.findByText("No calls between files were found.")).toBeTruthy();
    expect(renderDiagram).not.toHaveBeenCalled();
  });

  it("lists every file with its counts, linked to the file at the indexed commit", async () => {
    overviewApi();

    renderApp("/repositories/link-flask/overview");

    const fileLink = await screen.findByRole("link", { name: "src/flask/app.py" });
    expect(fileLink.getAttribute("href")).toBe(flaskOverview().files[0].link);
    const row = fileLink.closest("tr");
    expect(within(row).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "1",
      "40",
      "9",
      "2",
      "12",
    ]);
    expect(screen.getByRole("link", { name: "src/flask/py.typed" })).toBeTruthy();
  });

  it("explains what the arrows leave out", async () => {
    overviewApi();

    renderApp("/repositories/link-flask/overview");

    expect(await screen.findByText(/A missing arrow does\s+not prove two files are unrelated/)).toBeTruthy();
  });

  it("says what sample code was left out and that search still includes it", async () => {
    overviewApi();

    renderApp("/repositories/link-flask/overview");

    expect(
      await screen.findByText(
        /Sample code in examples\/ is left out of this overview \(10 files and\s+41 calls from or into them\)/,
      ),
    ).toBeTruthy();
    expect(screen.getByText(/Search and\s+answers still include it/)).toBeTruthy();
  });

  it("says nothing about left-out code when nothing was left out", async () => {
    overviewApi({ ...flaskOverview(), left_out: { folders: ["examples/"], files: 0, calls: 0 } });

    renderApp("/repositories/link-flask/overview");

    await screen.findByRole("link", { name: "src/flask/app.py" });
    expect(screen.queryByText(/is left out of this overview/)).toBeNull();
  });

  it("carries the license notice", async () => {
    overviewApi();

    renderApp("/repositories/link-flask/overview");

    expect(
      (await screen.findByRole("link", { name: "Read the license (LICENSE.txt)" })).getAttribute(
        "href",
      ),
    ).toBe(flaskOverview().repository.license.link);
  });

  it("releases the drawn image when leaving the page", async () => {
    overviewApi();
    const { unmount } = renderApp("/repositories/link-flask/overview");
    await screen.findByRole("img", { name: DIAGRAM_DESCRIPTION });

    unmount();

    await waitFor(() => expect(releasedUrls).toEqual(["blob:diagram-1"]));
  });

  it("shows the service's refusal", async () => {
    installFakeApi({
      "GET /api/me": SIGNED_IN,
      "GET /api/repositories/link-flask": jsonReply(200, readyLink()),
      "GET /api/repositories/link-flask/overview": jsonReply(503, {
        error: "The database is unavailable for now; try again shortly",
      }),
    });

    renderApp("/repositories/link-flask/overview");

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Could not load the overview: The database is unavailable for now; try again shortly",
    );
  });

  it("does not ask for an overview of a repository still indexing", async () => {
    const { calls } = installFakeApi({
      "GET /api/me": SIGNED_IN,
      "GET /api/repositories/link-itsdangerous": jsonReply(200, indexingLink()),
    });

    renderApp("/repositories/link-itsdangerous/overview");

    expect(await screen.findByText(/This repository is not ready yet/)).toBeTruthy();
    expect(calls.some((call) => call.key.endsWith("/overview"))).toBe(false);
  });

  it("is linked from a ready repository's card", async () => {
    installFakeApi({
      "GET /api/me": SIGNED_IN,
      "GET /api/repositories": jsonReply(200, { repositories: [readyLink()] }),
    });

    renderApp("/repositories");

    expect((await screen.findByRole("link", { name: "Overview" })).getAttribute("href")).toBe(
      "/repositories/link-flask/overview",
    );
  });
});
