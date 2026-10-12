import { describe, expect, it } from "vitest";
import {
  ApiRequestError,
  UNEXPECTED_MESSAGE,
  UNREACHABLE_MESSAGE,
  apiRequest,
} from "../src/api/client.js";
import { emptyReply, installFakeApi, jsonReply } from "./support/fakeApi.js";

async function caughtError(promise) {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("Expected the request to fail");
}

describe("apiRequest", () => {
  it("returns the parsed reply", async () => {
    installFakeApi({ "GET /api/things": jsonReply(200, { count: 2 }) });

    expect(await apiRequest("/api/things")).toEqual({ count: 2 });
  });

  it("returns null for a reply with no content", async () => {
    installFakeApi({ "POST /api/auth/logout": emptyReply(204) });

    expect(await apiRequest("/api/auth/logout", { method: "POST" })).toBeNull();
  });

  it("sends a JSON body with the cookie of the same site only", async () => {
    const { calls } = installFakeApi({ "POST /api/things": jsonReply(201, { ok: true }) });

    await apiRequest("/api/things", { method: "POST", body: { name: "flask" } });

    const { options } = calls[0];
    expect(options.credentials).toBe("same-origin");
    expect(options.headers["Content-Type"]).toBe("application/json");
    expect(options.body).toBe('{"name":"flask"}');
  });

  it("sends no body or content type when there is no body", async () => {
    const { calls } = installFakeApi({ "GET /api/things": jsonReply(200, {}) });

    await apiRequest("/api/things");

    const { options } = calls[0];
    expect(options.body).toBeUndefined();
    expect(options.headers["Content-Type"]).toBeUndefined();
  });

  it("raises the server's message, status, and retry time", async () => {
    installFakeApi({
      "GET /api/things": jsonReply(429, { error: "Daily limit reached" }, { "Retry-After": "120" }),
    });

    const error = await caughtError(apiRequest("/api/things"));

    expect(error).toBeInstanceOf(ApiRequestError);
    expect(error.status).toBe(429);
    expect(error.message).toBe("Daily limit reached");
    expect(error.retryAfterSeconds).toBe(120);
  });

  it("leaves the retry time empty when the header is missing or not a number", async () => {
    installFakeApi({
      "GET /api/missing": jsonReply(503, { error: "Busy" }),
      "GET /api/date": jsonReply(503, { error: "Busy" }, { "Retry-After": "Sun, 11 Oct 2026" }),
    });

    expect((await caughtError(apiRequest("/api/missing"))).retryAfterSeconds).toBeNull();
    expect((await caughtError(apiRequest("/api/date"))).retryAfterSeconds).toBeNull();
  });

  it("uses a general message when a failure is not JSON", async () => {
    installFakeApi({ "GET /api/things": jsonReply(502, "<html>Bad Gateway</html>") });

    const error = await caughtError(apiRequest("/api/things"));

    expect(error.status).toBe(502);
    expect(error.message).toBe(UNEXPECTED_MESSAGE);
  });

  it("uses a general message when a failure has no error text", async () => {
    installFakeApi({ "GET /api/things": jsonReply(500, { problem: "x" }) });

    expect((await caughtError(apiRequest("/api/things"))).message).toBe(UNEXPECTED_MESSAGE);
  });

  it("treats a successful reply that is not JSON as unexpected", async () => {
    installFakeApi({ "GET /api/things": jsonReply(200, "not json") });

    const error = await caughtError(apiRequest("/api/things"));

    expect(error.message).toBe(UNEXPECTED_MESSAGE);
  });

  it("reports a server that cannot be reached", async () => {
    installFakeApi({
      "GET /api/things": () => {
        throw new TypeError("Failed to fetch");
      },
    });

    const error = await caughtError(apiRequest("/api/things"));

    expect(error.status).toBe(0);
    expect(error.message).toBe(UNREACHABLE_MESSAGE);
    expect(error.isApiReply).toBe(false);
  });

  it("tells the API's own error replies apart from anything else", async () => {
    installFakeApi({
      "GET /api/own": jsonReply(503, { error: "Busy" }),
      "GET /api/gateway": jsonReply(503, "<html>Service Unavailable</html>"),
      "GET /api/page": jsonReply(200, "<html>Waking up</html>"),
    });

    expect((await caughtError(apiRequest("/api/own"))).isApiReply).toBe(true);
    expect((await caughtError(apiRequest("/api/gateway"))).isApiReply).toBe(false);
    expect((await caughtError(apiRequest("/api/page"))).isApiReply).toBe(false);
  });
});
