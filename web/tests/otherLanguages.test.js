import { describe, expect, it } from "vitest";
import { describeOtherLanguages } from "../src/repositories/otherLanguages.js";

describe("describing files in other languages", () => {
  it("has nothing to say when there are none or none were counted", () => {
    expect(describeOtherLanguages([])).toBeNull();
    expect(describeOtherLanguages(null)).toBeNull();
    expect(describeOtherLanguages(undefined)).toBeNull();
  });

  it("writes one, two, or more languages as prose with counts", () => {
    expect(describeOtherLanguages([{ language: "Shell", files: 1 }])).toBe("1 Shell file");
    expect(
      describeOtherLanguages([
        { language: "C", files: 214 },
        { language: "C++", files: 12 },
      ]),
    ).toBe("214 C files and 12 C++ files");
    expect(
      describeOtherLanguages([
        { language: "C", files: 1200 },
        { language: "Cython", files: 3 },
        { language: "Shell", files: 1 },
      ]),
    ).toBe("1,200 C files, 3 Cython files and 1 Shell file");
  });
});
