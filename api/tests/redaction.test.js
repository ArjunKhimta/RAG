const {
  REDACTION_PLACEHOLDER,
  clearRegisteredSecrets,
  redact,
  redactError,
  registerSecret,
} = require("../src/redaction");

afterEach(() => {
  clearRegisteredSecrets();
});

test("a registered secret is replaced everywhere it appears", () => {
  registerSecret("s3cret-value-123");

  const redacted = redact("first s3cret-value-123 and again s3cret-value-123");

  expect(redacted).toBe(`first ${REDACTION_PLACEHOLDER} and again ${REDACTION_PLACEHOLDER}`);
});

test("a value shorter than eight characters is not registered", () => {
  registerSecret("short");

  expect(redact("a short word")).toBe("a short word");
});

test("an empty or missing value is ignored", () => {
  registerSecret("");
  registerSecret(undefined);

  expect(redact("nothing to hide")).toBe("nothing to hide");
});

test("the longest secret is replaced first, so a shorter one inside it leaves no trace", () => {
  registerSecret("abcdefgh");
  registerSecret("abcdefgh-ijklmnop");

  expect(redact("token abcdefgh-ijklmnop")).toBe(`token ${REDACTION_PLACEHOLDER}`);
});

test("credentials inside a connection URI are removed even when never registered", () => {
  const text = "failed to reach mongodb+srv://user:p4ss@cluster0.example.net/db";

  expect(redact(text)).toBe(
    `failed to reach mongodb+srv://${REDACTION_PLACEHOLDER}@cluster0.example.net/db`,
  );
});

test("an error's message and stack are redacted", () => {
  registerSecret("s3cret-value-123");
  const error = new Error("rejected s3cret-value-123");

  const description = redactError(error);

  expect(description).toContain(`Error: rejected ${REDACTION_PLACEHOLDER}`);
  expect(description).not.toContain("s3cret-value-123");
});

test("something thrown that is not an Error is described and redacted", () => {
  registerSecret("s3cret-value-123");

  expect(redactError("plain s3cret-value-123")).toBe(`plain ${REDACTION_PLACEHOLDER}`);
});
