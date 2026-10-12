import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError, apiRequest } from "../src/api/client.js";
import { WAKE_INTERVAL_MILLISECONDS, WAKE_URLS, wakeServers } from "../src/api/wakeServer.js";

const LIVE_HOSTNAME = "repositorycite.vercel.app";

const WAKE_OPTIONS = {
  mode: "no-cors",
  credentials: "omit",
  cache: "no-store",
  referrerPolicy: "no-referrer",
};

let clock = 1_000_000_000;

function laterThanAnyWake() {
  clock += WAKE_INTERVAL_MILLISECONDS * 10;
  return clock;
}

function installFakeFetch(reply) {
  const fetchMock = vi.fn(reply);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function wakeCalls(fetchMock) {
  return fetchMock.mock.calls.filter(([url]) => WAKE_URLS.includes(url));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("waking the servers", () => {
  it("calls both health checks directly, without cookies, a referrer, or reading the reply", () => {
    const fetchMock = installFakeFetch(async () => new Response(null, { status: 200 }));

    wakeServers(LIVE_HOSTNAME, laterThanAnyWake());

    expect(WAKE_URLS).toEqual([
      "https://repositorycite-api.onrender.com/health",
      "https://repositorycite-retrieval.onrender.com/health",
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenCalledWith(WAKE_URLS[0], WAKE_OPTIONS);
    expect(fetchMock).toHaveBeenCalledWith(WAKE_URLS[1], WAKE_OPTIONS);
  });

  it("wakes at most once in each interval", () => {
    const fetchMock = installFakeFetch(async () => new Response(null, { status: 200 }));
    const first = laterThanAnyWake();

    wakeServers(LIVE_HOSTNAME, first);
    wakeServers(LIVE_HOSTNAME, first + WAKE_INTERVAL_MILLISECONDS - 1);
    expect(fetchMock).toHaveBeenCalledTimes(2);

    wakeServers(LIVE_HOSTNAME, first + WAKE_INTERVAL_MILLISECONDS);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it.each(["localhost", "127.0.0.1", "[::1]"])("never reaches the live servers from %s", (hostname) => {
    const fetchMock = installFakeFetch(async () => new Response(null, { status: 200 }));

    wakeServers(hostname, laterThanAnyWake());

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("ignores a failed wake-up call", async () => {
    const failure = Promise.reject(new TypeError("Failed to fetch"));
    installFakeFetch(() => failure);

    expect(() => wakeServers(LIVE_HOSTNAME, laterThanAnyWake())).not.toThrow();
    await failure.catch(() => {});
  });
});

describe("a request that finds a server asleep", () => {
  function onLiveSite() {
    vi.stubGlobal("location", { ...window.location, hostname: LIVE_HOSTNAME });
    vi.spyOn(Date, "now").mockReturnValue(laterThanAnyWake());
  }

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each([502, 503, 504])("wakes both servers after a %s", async (status) => {
    onLiveSite();
    const fetchMock = installFakeFetch(async () => new Response("", { status }));

    await expect(apiRequest("/api/me")).rejects.toBeInstanceOf(ApiRequestError);

    expect(wakeCalls(fetchMock)).toHaveLength(2);
  });

  it("wakes both servers when the site's API cannot be reached", async () => {
    onLiveSite();
    const fetchMock = installFakeFetch(async (url) => {
      if (WAKE_URLS.includes(url)) {
        return new Response(null, { status: 200 });
      }
      throw new TypeError("Failed to fetch");
    });

    await expect(apiRequest("/api/me")).rejects.toMatchObject({ status: 0 });

    expect(wakeCalls(fetchMock)).toHaveLength(2);
  });

  it.each([400, 401, 404, 409, 429, 500])("does not wake anything after a %s", async (status) => {
    onLiveSite();
    const fetchMock = installFakeFetch(async () => new Response("", { status }));

    await expect(apiRequest("/api/me")).rejects.toBeInstanceOf(ApiRequestError);

    expect(wakeCalls(fetchMock)).toHaveLength(0);
  });
});
