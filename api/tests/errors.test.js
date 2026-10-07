const express = require("express");
const request = require("supertest");

const {
  INTERNAL_ERROR_MESSAGE,
  UNREADABLE_REQUEST_MESSAGE,
  registerErrorReplies,
} = require("../src/errors");
const {
  REDACTION_PLACEHOLDER,
  clearRegisteredSecrets,
  registerSecret,
} = require("../src/redaction");
const { RetrievalServiceError } = require("../src/retrievalService");

const SECRET = "s3cret-token-value";

let errorLog;

beforeEach(() => {
  errorLog = jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  errorLog.mockRestore();
  clearRegisteredSecrets();
});

function appThrowing(error) {
  const app = express();
  app.get("/fails", () => {
    throw error;
  });
  app.get("/fails-later", async () => {
    await Promise.resolve();
    throw error;
  });
  registerErrorReplies(app, 16 * 1024);
  return app;
}

function loggedText() {
  return errorLog.mock.calls.map((call) => call.join(" ")).join("\n");
}

test("an unexpected error is a generic 500 whose details reach the log redacted", async () => {
  registerSecret(SECRET);
  const error = new Error(`could not reach mongodb+srv://user:pw@host/db with ${SECRET}`);

  const response = await request(appThrowing(error)).get("/fails");

  expect(response.status).toBe(500);
  expect(response.body).toEqual({ error: INTERNAL_ERROR_MESSAGE });
  expect(JSON.stringify(response.body)).not.toContain(SECRET);
  expect(loggedText()).toContain("Unexpected error on GET /fails");
  expect(loggedText()).toContain(REDACTION_PLACEHOLDER);
  expect(loggedText()).not.toContain(SECRET);
  expect(loggedText()).not.toContain("user:pw");
});

test("an error thrown after an await is caught the same way", async () => {
  const response = await request(appThrowing(new Error("later"))).get("/fails-later");

  expect(response.status).toBe(500);
  expect(response.body).toEqual({ error: INTERNAL_ERROR_MESSAGE });
});

test("a client error from the body reader gets our message, not its text", async () => {
  const unsupported = Object.assign(new Error(`unsupported charset ${SECRET}`), {
    status: 415,
    expose: true,
    type: "charset.unsupported",
  });

  const response = await request(appThrowing(unsupported)).get("/fails");

  expect(response.status).toBe(415);
  expect(response.body).toEqual({ error: UNREADABLE_REQUEST_MESSAGE });
  expect(errorLog).not.toHaveBeenCalled();
});

test("an error marked as a server error is never shown, even if marked exposable", async () => {
  const serverSide = Object.assign(new Error("internal detail"), { status: 503, expose: true });

  const response = await request(appThrowing(serverSide)).get("/fails");

  expect(response.status).toBe(500);
  expect(response.body).toEqual({ error: INTERNAL_ERROR_MESSAGE });
});

test("a retrieval service error replies with its status, message, and Retry-After", async () => {
  const quotaUsedUp = new RetrievalServiceError(503, "Gemini's daily quota is used up", 41820);

  const response = await request(appThrowing(quotaUsedUp)).get("/fails");

  expect(response.status).toBe(503);
  expect(response.headers["retry-after"]).toBe("41820");
  expect(response.body).toEqual({ error: "Gemini's daily quota is used up" });
  expect(errorLog).not.toHaveBeenCalled();
});

test("a retrieval service error without a wait sends no Retry-After", async () => {
  const response = await request(appThrowing(new RetrievalServiceError(409, "Not indexed"))).get(
    "/fails",
  );

  expect(response.status).toBe(409);
  expect(response.headers["retry-after"]).toBeUndefined();
  expect(response.body).toEqual({ error: "Not indexed" });
});
