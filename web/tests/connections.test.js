import { describe, expect, it } from "vitest";
import { connectedFiles, connectionsOf } from "../src/overview/connections.js";

const LINKS = [
  { from: "app.py", to: "helpers.py", calls: 9 },
  { from: "helpers.py", to: "app.py", calls: 2 },
  { from: "sessions.py", to: "helpers.py", calls: 2 },
  { from: "cli.py", to: "app.py", calls: 2 },
];

describe("file connections", () => {
  it("lists every linked file once, most calls first, ties by path", () => {
    expect(connectedFiles(LINKS)).toEqual([
      { path: "app.py", calls: 13 },
      { path: "helpers.py", calls: 13 },
      { path: "cli.py", calls: 2 },
      { path: "sessions.py", calls: 2 },
    ]);
  });

  it("splits a file's links into the files it calls and the files that call it", () => {
    expect(connectionsOf("app.py", LINKS)).toEqual({
      outgoing: [{ path: "helpers.py", calls: 9 }],
      incoming: [
        { path: "cli.py", calls: 2 },
        { path: "helpers.py", calls: 2 },
      ],
    });
  });

  it("gives empty lists for a file with no links", () => {
    expect(connectionsOf("py.typed", LINKS)).toEqual({ outgoing: [], incoming: [] });
  });
});
