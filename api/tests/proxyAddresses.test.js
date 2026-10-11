const request = require("supertest");
const { readProxySettings, InvalidConfigError } = require("../src/config");
const { buildTestApp } = require("./support/testApp");

function checkForwarding(app, forwardedFor) {
  const call = request(app).get("/api/forwarding-check");
  return forwardedFor === undefined ? call : call.set("X-Forwarded-For", forwardedFor);
}

describe("proxy settings", () => {
  test("no proxies are trusted and the check route is off unless set", () => {
    expect(readProxySettings({})).toEqual({ trustProxyHops: 0, forwardingCheck: false });
    expect(readProxySettings({ TRUST_PROXY_HOPS: "", FORWARDING_CHECK: "" })).toEqual({
      trustProxyHops: 0,
      forwardingCheck: false,
    });
  });

  test("the hops and the check route come from the environment", () => {
    expect(readProxySettings({ TRUST_PROXY_HOPS: "2", FORWARDING_CHECK: "on" })).toEqual({
      trustProxyHops: 2,
      forwardingCheck: true,
    });
  });

  test.each(["4", "-1", "1.5", " 1", "two", "1e0"])("TRUST_PROXY_HOPS=%j stops start-up", (text) => {
    expect(() => readProxySettings({ TRUST_PROXY_HOPS: text })).toThrow(
      new InvalidConfigError("TRUST_PROXY_HOPS", "must be a whole number from 0 to 3"),
    );
  });

  test.each(["yes", "true", "ON", "1"])("FORWARDING_CHECK=%j stops start-up", (text) => {
    expect(() => readProxySettings({ FORWARDING_CHECK: text })).toThrow(InvalidConfigError);
  });
});

describe("the visitor's address", () => {
  test("with no proxies trusted, a forged X-Forwarded-For is ignored", async () => {
    const { app } = buildTestApp({ forwardingCheck: true });

    const response = await checkForwarding(app, "203.0.113.7");

    expect(response.body.forwarded_for).toEqual(["203.0.113.7"]);
    expect(response.body.address_used).toBe(response.body.connection_address);
  });

  test("with one proxy trusted, the address it added is the visitor's", async () => {
    const { app } = buildTestApp({ trustProxyHops: 1, forwardingCheck: true });

    const response = await checkForwarding(app, "198.51.100.20");

    expect(response.body.address_used).toBe("198.51.100.20");
    expect(response.body.trust_proxy_hops).toBe(1);
  });

  test("with two proxies trusted, an address the visitor wrote at the left is not believed", async () => {
    const { app } = buildTestApp({ trustProxyHops: 2, forwardingCheck: true });

    const response = await checkForwarding(app, "10.9.9.9, 198.51.100.20, 192.0.2.1");

    expect(response.body.forwarded_for).toEqual(["10.9.9.9", "198.51.100.20", "192.0.2.1"]);
    expect(response.body.address_used).toBe("198.51.100.20");
  });

  test("behind a trusted proxy, each visitor has their own sign-in limit", async () => {
    const { app } = buildTestApp({ trustProxyHops: 1 });
    const openLoginLinkFrom = (address) =>
      request(app).get("/api/auth/github/login").set("X-Forwarded-For", address);
    for (let index = 0; index < 20; index += 1) {
      await openLoginLinkFrom("198.51.100.20");
    }

    const sameVisitor = await openLoginLinkFrom("198.51.100.20");
    const otherVisitor = await openLoginLinkFrom("198.51.100.21");

    expect(sameVisitor.headers.location).toContain("sign_in=rate_limited");
    expect(new URL(otherVisitor.headers.location).hostname).toBe("github.example");
  });
});

describe("the forwarding check route", () => {
  test("is a plain 404 unless turned on", async () => {
    const { app } = buildTestApp();

    const response = await checkForwarding(app, "203.0.113.7");

    expect(response.status).toBe(404);
  });

  test("is never cached and needs no sign-in", async () => {
    const { app } = buildTestApp({ forwardingCheck: true });

    const response = await checkForwarding(app);

    expect(response.status).toBe(200);
    expect(response.headers["cache-control"]).toBe("no-store");
    expect(response.body.forwarded_for).toEqual([]);
  });
});
