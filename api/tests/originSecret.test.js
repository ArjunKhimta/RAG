const request = require("supertest");
const { InvalidConfigError, MissingConfigError, readOriginSecret } = require("../src/config");
const { NOT_THROUGH_SITE_MESSAGE } = require("../src/originSecret");
const { clearRegisteredSecrets } = require("../src/redaction");
const { TEST_APP_ORIGIN, buildTestApp } = require("./support/testApp");

const ORIGIN_SECRET = "origin-secret-for-tests-0123456789abcdef";

afterEach(() => {
  clearRegisteredSecrets();
});

describe("the origin secret setting", () => {
  test("is required for a public https site", () => {
    expect(() => readOriginSecret({}, "https://repositorycite.vercel.app")).toThrow(
      new MissingConfigError("ORIGIN_SECRET"),
    );
  });

  test("must be at least 32 characters", () => {
    expect(() =>
      readOriginSecret({ ORIGIN_SECRET: "too-short" }, "https://repositorycite.vercel.app"),
    ).toThrow(InvalidConfigError);
  });

  test("is off for a local site unless set", () => {
    expect(readOriginSecret({}, "http://localhost:5173")).toBeNull();
    expect(readOriginSecret({ ORIGIN_SECRET }, "http://localhost:5173")).toBe(ORIGIN_SECRET);
  });
});

describe("with the origin secret on", () => {
  const { app } = buildTestApp({ originSecret: ORIGIN_SECRET });

  test.each([
    ["no secret", undefined],
    ["a wrong secret", "wrong-secret-of-another-length"],
    ["a secret one character off", `${ORIGIN_SECRET.slice(0, -1)}X`],
  ])("a request with %s is refused before any route", async (description, secret) => {
    const call = request(app).get("/api/me");
    const response = await (secret === undefined ? call : call.set("X-Origin-Secret", secret));

    expect(response.status).toBe(403);
    expect(response.body).toEqual({ error: NOT_THROUGH_SITE_MESSAGE });
    expect(response.headers["cache-control"]).toBe("no-store");
  });

  test("a request with the secret reaches the routes", async () => {
    const response = await request(app).get("/api/me").set("X-Origin-Secret", ORIGIN_SECRET);

    expect(response.status).toBe(401);
  });

  test("a body sent without the secret is refused before it is read", async () => {
    const response = await request(app)
      .post("/api/auth/logout")
      .set("Origin", TEST_APP_ORIGIN)
      .set("Content-Type", "application/json")
      .send("{not json");

    expect(response.status).toBe(403);
  });

  test("the health check stays open for the host", async () => {
    const response = await request(app).get("/health");

    expect(response.status).toBe(200);
  });
});

describe("caching", () => {
  test.each([
    ["a refusal", "/api/me", 401],
    ["an unknown API path", "/api/nothing-here", 404],
  ])("%s is never kept by a browser or CDN", async (description, path, status) => {
    const { app } = buildTestApp();

    const response = await request(app).get(path);

    expect(response.status).toBe(status);
    expect(response.headers["cache-control"]).toBe("no-store");
  });

  test("a signed-in reply is never kept either", async () => {
    const built = buildTestApp();
    const cookie = await built.sessionCookieFor({
      githubId: 1,
      login: "octocat",
      name: null,
      avatarUrl: null,
    });

    const response = await request(built.app).get("/api/me").set("Cookie", cookie);

    expect(response.status).toBe(200);
    expect(response.headers["cache-control"]).toBe("no-store");
  });
});
