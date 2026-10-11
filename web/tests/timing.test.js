import { describe, expect, it } from "vitest";
import { cancelTiming, finishTiming, startTiming, timed } from "../src/timing.js";

const NAME = "example step";

function measures() {
  return performance.getEntriesByName(NAME, "measure");
}

describe("timing", () => {
  it("records one measure from start to finish and forgets the start", () => {
    startTiming(NAME);
    finishTiming(NAME);

    expect(measures()).toHaveLength(1);
    expect(performance.getEntriesByName(`${NAME} start`, "mark")).toHaveLength(0);
  });

  it("records nothing when finishing a step that never started", () => {
    finishTiming(NAME);

    expect(measures()).toHaveLength(0);
  });

  it("records nothing for a cancelled step", () => {
    startTiming(NAME);
    cancelTiming(NAME);
    finishTiming(NAME);

    expect(measures()).toHaveLength(0);
  });

  it("measures from the latest start when a step starts again", () => {
    startTiming(NAME);
    startTiming(NAME);
    finishTiming(NAME);
    finishTiming(NAME);

    expect(measures()).toHaveLength(1);
  });

  it("times work that succeeds and passes its result on", async () => {
    const result = await timed(NAME, async () => "drawn");

    expect(result).toBe("drawn");
    expect(measures()).toHaveLength(1);
  });

  it("records nothing for work that fails and passes the failure on", async () => {
    await expect(
      timed(NAME, async () => {
        throw new Error("Parse error");
      }),
    ).rejects.toThrow("Parse error");

    expect(measures()).toHaveLength(0);
    expect(performance.getEntriesByName(`${NAME} start`, "mark")).toHaveLength(0);
  });
});
