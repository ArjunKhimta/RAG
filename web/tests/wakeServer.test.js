import { afterEach, describe, expect, it, vi } from "vitest";
import { WAKE_URL, wakeServer } from "../src/api/wakeServer.js";

function installFakeFetch(reply) {
  const fetchMock = vi.fn(reply);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("waking the API", () => {
  it("calls the API's health check directly, without cookies, a referrer, or reading the reply", () => {
    const fetchMock = installFakeFetch(async () => new Response(null, { status: 200 }));

    wakeServer("repositorycite.vercel.app");

    expect(WAKE_URL).toBe("https://repositorycite-api.onrender.com/health");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(WAKE_URL, {
      mode: "no-cors",
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
    });
  });

  it.each(["localhost", "127.0.0.1", "[::1]"])("never reaches the live API from %s", (hostname) => {
    const fetchMock = installFakeFetch(async () => new Response(null, { status: 200 }));

    wakeServer(hostname);

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("ignores a failed wake-up call", async () => {
    const failure = Promise.reject(new TypeError("Failed to fetch"));
    installFakeFetch(() => failure);

    expect(() => wakeServer("repositorycite.vercel.app")).not.toThrow();
    await failure.catch(() => {});
  });
});
