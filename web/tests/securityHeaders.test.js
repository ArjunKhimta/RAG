import { describe, expect, it } from "vitest";
import {
  API_ORIGIN,
  API_PROXY_TARGET,
  BUILT_SITE_HEADERS,
  CONTENT_SECURITY_POLICY,
  RETRIEVAL_SERVICE_ORIGIN,
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

  it("runs only scripts served by the site itself", () => {
    expect(policy["script-src"]).toEqual(["'self'"]);
    expect(CONTENT_SECURITY_POLICY).not.toContain("unsafe-eval");
  });

  it("allows inline styles, which Mermaid writes while measuring diagram labels", () => {
    expect(policy["style-src"]).toEqual(["'self'", "'unsafe-inline'"]);
  });

  it("allows inline content for styles only", () => {
    const directivesWithInline = Object.keys(policy).filter((name) =>
      policy[name].includes("'unsafe-inline'"),
    );
    expect(directivesWithInline).toEqual(["style-src"]);
  });

  it("allows images only from the site, diagrams drawn in the page, and GitHub avatars", () => {
    expect(policy["img-src"]).toEqual([
      "'self'",
      "blob:",
      "https://avatars.githubusercontent.com",
    ]);
  });

  it("sends requests only to the site itself, and to the two servers' own addresses to wake them", () => {
    expect(policy["connect-src"]).toEqual([
      "'self'",
      "https://repositorycite-api.onrender.com",
      "https://repositorycite-retrieval.onrender.com",
    ]);
    expect(policy["connect-src"][1]).toBe(API_ORIGIN);
    expect(policy["connect-src"][2]).toBe(RETRIEVAL_SERVICE_ORIGIN);
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
