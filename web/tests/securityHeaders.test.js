import { describe, expect, it } from "vitest";
import {
  API_PROXY_TARGET,
  BUILT_SITE_HEADERS,
  CONTENT_SECURITY_POLICY,
  SHARED_SECURITY_HEADERS,
} from "../securityHeaders.js";

function directives(policy) {
  return Object.fromEntries(
    policy.split("; ").map((directive) => {
      const [name, ...values] = directive.split(" ");
      return [name, values];
    }),
  );
}

describe("content security policy", () => {
  const policy = directives(CONTENT_SECURITY_POLICY);

  it("runs only scripts and styles served by the site itself", () => {
    expect(policy["script-src"]).toEqual(["'self'"]);
    expect(policy["style-src"]).toEqual(["'self'"]);
    expect(CONTENT_SECURITY_POLICY).not.toContain("unsafe-inline");
    expect(CONTENT_SECURITY_POLICY).not.toContain("unsafe-eval");
  });

  it("allows images only from the site, diagrams drawn in the page, and GitHub avatars", () => {
    expect(policy["img-src"]).toEqual([
      "'self'",
      "blob:",
      "https://avatars.githubusercontent.com",
    ]);
  });

  it("sends requests only to the site itself", () => {
    expect(policy["connect-src"]).toEqual(["'self'"]);
  });

  it("refuses plugins, base changes, and framing", () => {
    expect(policy["object-src"]).toEqual(["'none'"]);
    expect(policy["base-uri"]).toEqual(["'none'"]);
    expect(policy["frame-ancestors"]).toEqual(["'none'"]);
  });
});

describe("security headers", () => {
  it("adds the content security policy only to the built site", () => {
    expect(SHARED_SECURITY_HEADERS["Content-Security-Policy"]).toBeUndefined();
    expect(BUILT_SITE_HEADERS["Content-Security-Policy"]).toBe(CONTENT_SECURITY_POLICY);
  });

  it("keeps the shared headers on the built site", () => {
    expect(BUILT_SITE_HEADERS).toMatchObject(SHARED_SECURITY_HEADERS);
  });

  it("sends no referrer to linked sites", () => {
    expect(SHARED_SECURITY_HEADERS["Referrer-Policy"]).toBe("no-referrer");
  });

  it("proxies the API to the local Node server", () => {
    expect(API_PROXY_TARGET).toBe("http://127.0.0.1:3000");
  });
});
