import { describe, expect, it } from "vitest";
import { gitHubCommitLink, safeGitHubLink } from "../src/links.js";
import {
  normalizeRepositoryInput,
  resumeAvailability,
  stageDescription,
  statusLabel,
} from "../src/repositories/linkStatus.js";
import { describeWait, messageWithWait } from "../src/time.js";
import { failedLink, indexingLink, readyLink, waitingLink } from "./support/repositories.js";

const NOW = Date.parse("2026-10-11T12:00:00.000Z");

describe("resumeAvailability", () => {
  it("allows resuming an interrupted link", () => {
    expect(resumeAvailability(readyLink({ status: "interrupted" }), NOW).canResume).toBe(true);
  });

  it.each(["clone_failed", "unexpected"])("allows retrying a failure coded %s", (code) => {
    expect(resumeAvailability(failedLink(code, "x"), NOW).canResume).toBe(true);
  });

  it.each(["not_found", "not_public", "license_not_allowed", "too_large", "too_many_chunks"])(
    "refuses retrying a permanent failure coded %s",
    (code) => {
      expect(resumeAvailability(failedLink(code, "x"), NOW).canResume).toBe(false);
    },
  );

  it("waits for the quota reset, then allows resuming", () => {
    const resumeAfter = "2026-10-12T07:00:00.000Z";

    expect(resumeAvailability(waitingLink(resumeAfter), NOW)).toEqual({
      canResume: false,
      resumeAfter,
    });
    expect(resumeAvailability(waitingLink(resumeAfter), Date.parse(resumeAfter)).canResume).toBe(
      true,
    );
  });

  it("never offers to resume a ready or indexing link", () => {
    expect(resumeAvailability(readyLink(), NOW).canResume).toBe(false);
    expect(resumeAvailability(indexingLink(), NOW).canResume).toBe(false);
  });
});

describe("normalizeRepositoryInput", () => {
  it.each([
    ["pallets/flask", "pallets/flask"],
    ["  pallets/flask  ", "pallets/flask"],
    ["https://github.com/pallets/flask", "pallets/flask"],
    ["https://www.github.com/pallets/flask/", "pallets/flask"],
    ["github.com/pallets/flask.git", "pallets/flask"],
    ["HTTPS://GitHub.com/Pallets/Flask", "Pallets/Flask"],
  ])("turns %j into %j", (typed, expected) => {
    expect(normalizeRepositoryInput(typed)).toBe(expected);
  });

  it("leaves other sites' addresses for the server to refuse", () => {
    expect(normalizeRepositoryInput("https://gitlab.com/a/b")).toBe("https://gitlab.com/a/b");
  });
});

describe("labels", () => {
  it("describes each indexing stage, and an unknown one as starting", () => {
    expect(stageDescription("cloning")).toBe("Downloading the repository from GitHub");
    expect(stageDescription("call graph")).toBe("Working out which functions call which");
    expect(stageDescription(null)).toBe("Starting");
    expect(stageDescription("toString")).toBe("Starting");
  });

  it("labels statuses, passing an unknown one through", () => {
    expect(statusLabel("waiting_for_quota")).toBe("Paused until the daily quota resets");
    expect(statusLabel("something_new")).toBe("something_new");
  });
});

describe("waits", () => {
  it.each([
    [30, "less than a minute"],
    [60, "about 1 minute"],
    [125, "about 2 minutes"],
    [3600, "about 1 hour"],
    [5 * 3600 + 600, "about 5 hours"],
  ])("describes %i seconds as %j", (seconds, expected) => {
    expect(describeWait(seconds)).toBe(expected);
  });

  it("adds the wait to an error message only when there is one", () => {
    expect(messageWithWait({ message: "Busy", retryAfterSeconds: 60 })).toBe(
      "Busy (in about 1 minute).",
    );
    expect(messageWithWait({ message: "Bad name", retryAfterSeconds: null })).toBe("Bad name");
  });
});

describe("GitHub links", () => {
  it("accepts only GitHub addresses from the server", () => {
    expect(safeGitHubLink("https://github.com/pallets/flask/blob/abc/LICENSE")).toBe(
      "https://github.com/pallets/flask/blob/abc/LICENSE",
    );
    expect(safeGitHubLink("javascript:alert(1)")).toBeNull();
    expect(safeGitHubLink("https://github.com.evil.example/x")).toBeNull();
    expect(safeGitHubLink(undefined)).toBeNull();
  });

  it("links a commit's tree", () => {
    expect(gitHubCommitLink("pallets/flask", "abc123")).toBe(
      "https://github.com/pallets/flask/tree/abc123",
    );
  });
});
