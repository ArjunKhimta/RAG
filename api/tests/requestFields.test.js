const { ApiError } = require("../src/apiError");
const {
  booleanField,
  requireFields,
  textField,
  wholeNumberField,
} = require("../src/requestFields");

function refusal(check) {
  try {
    check();
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(400);
    return error.message;
  }
  throw new Error("expected a refusal");
}

test.each([null, [], "text", 7])("body %p is not an object", (body) => {
  expect(refusal(() => requireFields(body, ["question"]))).toBe("The body must be a JSON object");
});

test("unknown fields are named in order, before missing ones", () => {
  expect(refusal(() => requireFields({ zeta: 1, alpha: 2 }, ["question"]))).toBe(
    "Unknown fields: alpha, zeta",
  );
  expect(refusal(() => requireFields({ limit: 1 }, ["query"], ["limit"]))).toBe(
    "Missing fields: query",
  );
});

test("text is trimmed, never blank, and at most 1,000 characters after trimming", () => {
  expect(textField("  why?  ", "question")).toBe("why?");
  expect(textField(` ${"q".repeat(1000)} `, "question")).toHaveLength(1000);
  expect(refusal(() => textField("q".repeat(1001), "question"))).toBe(
    "question must be at most 1000 characters",
  );
  expect(refusal(() => textField(7, "question"))).toBe("question must be a non-blank string");
});

test("booleans and whole numbers are checked strictly", () => {
  expect(booleanField(false, "exclude_tests")).toBe(false);
  expect(refusal(() => booleanField(0, "exclude_tests"))).toBe("exclude_tests must be true or false");
  expect(wholeNumberField(20, "limit", 1, 20)).toBe(20);
  expect(refusal(() => wholeNumberField("3", "limit", 1, 20))).toBe(
    "limit must be a whole number from 1 to 20",
  );
});
