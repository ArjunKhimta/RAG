const request = require("supertest");

const { MAX_REQUEST_BYTES } = require("../src/app");
const { INVALID_JSON_MESSAGE, NOT_FOUND_MESSAGE } = require("../src/errors");
const { buildTestApp } = require("./support/testApp");

function createApp() {
  return buildTestApp().app;
}

test("the health check answers ok", async () => {
  const response = await request(createApp()).get("/health");

  expect(response.status).toBe(200);
  expect(response.body).toEqual({ status: "ok" });
});

test("replies carry security headers and do not announce Express", async () => {
  const response = await request(createApp()).get("/health");

  expect(response.headers["x-content-type-options"]).toBe("nosniff");
  expect(response.headers["content-security-policy"]).toBe(
    "default-src 'none';frame-ancestors 'none'",
  );
  expect(response.headers["x-powered-by"]).toBeUndefined();
});

test("an unknown path is a JSON 404", async () => {
  const response = await request(createApp()).get("/no-such-path");

  expect(response.status).toBe(404);
  expect(response.body).toEqual({ error: NOT_FOUND_MESSAGE });
});

test("a body over 16 KB is refused with 413 before any route sees it", async () => {
  const oversized = { text: "x".repeat(MAX_REQUEST_BYTES) };

  const response = await request(createApp()).post("/health").send(oversized);

  expect(response.status).toBe(413);
  expect(response.body).toEqual({ error: "The request body is larger than 16 KB" });
});

test("a body that is not valid JSON is a 400 with our own message", async () => {
  const response = await request(createApp())
    .post("/health")
    .set("Content-Type", "application/json")
    .send('{"question": ');

  expect(response.status).toBe(400);
  expect(response.body).toEqual({ error: INVALID_JSON_MESSAGE });
});
