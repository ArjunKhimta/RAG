// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { BUILT_SITE_HEADERS } from "../securityHeaders.js";
import {
  API_PATH_PATTERN,
  API_ROUTE_SOURCE,
  ORIGIN_SECRET_HEADER,
  ASSETS_PATTERN,
  IMMUTABLE_CACHE,
  PAGE_FALLBACK_PATTERN,
  SITE_HEADERS_PATTERN,
  apiOriginOf,
  vercelConfig,
} from "../vercelConfig.js";

const committed = JSON.parse(readFileSync(new URL("../vercel.json", import.meta.url), "utf8"));

function pathPattern(source) {
  return new RegExp(`^${source.replace("/:path*", "(/.*)?")}$`);
}

describe("the committed vercel.json", () => {
  it("is exactly what the generator makes for its API address, headers included", () => {
    expect(committed).toEqual(vercelConfig(apiOriginOf(committed)));
  });

  it("forwards /api to an https address, keeping the rest of the path", () => {
    const apiRoute = committed.routes.find((route) => route.src === API_ROUTE_SOURCE);

    expect(apiRoute.dest).toMatch(/^https:\/\/[a-z0-9.-]+\/api\/\$1$/);
    expect(new RegExp(API_ROUTE_SOURCE).exec("/api/auth/github/callback")[1]).toBe(
      "auth/github/callback",
    );
  });

  it("replaces any origin secret a visitor sends with the project's own", () => {
    const [deletion, setting] = committed.routes[0].transforms;

    expect(deletion).toEqual({
      type: "request.headers",
      op: "delete",
      target: { key: ORIGIN_SECRET_HEADER },
    });
    expect(setting).toMatchObject({ op: "set", target: { key: ORIGIN_SECRET_HEADER } });
    expect(setting.args).toBe("$ORIGIN_SECRET");
    expect(setting.env).toEqual(["ORIGIN_SECRET"]);
  });

  it("never caches the API's replies", () => {
    const apiRule = committed.headers.find((rule) => rule.source === API_PATH_PATTERN);

    expect(apiRule.headers).toEqual([{ key: "x-vercel-enable-rewrite-caching", value: "0" }]);
  });

  it("installs from the lockfile without install scripts", () => {
    expect(committed.installCommand).toBe("npm ci --ignore-scripts");
  });
});

describe("the generated configuration", () => {
  const config = vercelConfig("https://api.example.onrender.com");

  it("sends every site header, the content security policy among them, on the site's pages", () => {
    const siteHeaders = config.headers.find((rule) => rule.source === SITE_HEADERS_PATTERN);

    expect(Object.fromEntries(siteHeaders.headers.map(({ key, value }) => [key, value]))).toEqual(
      BUILT_SITE_HEADERS,
    );
  });

  it.each([
    ["/", true],
    ["/repositories", true],
    ["/conversations/abc123", true],
    ["/api/me", false],
    ["/assets/index-abc123.js", false],
  ])("%s falls back to index.html: %s", (path, fallsBack) => {
    expect(pathPattern(PAGE_FALLBACK_PATTERN).test(path)).toBe(fallsBack);
  });

  it("leaves the API's own replies to the API's headers", () => {
    expect(pathPattern(SITE_HEADERS_PATTERN).test("/api/me")).toBe(false);
    expect(pathPattern(SITE_HEADERS_PATTERN).test("/privacy")).toBe(true);
  });

  it("caches the hashed build files for a year without checking again", () => {
    const assetRule = config.headers.find((rule) => rule.source === ASSETS_PATTERN);

    expect(assetRule.headers).toEqual([{ key: "Cache-Control", value: IMMUTABLE_CACHE }]);
  });

  it.each([
    "http://api.example.onrender.com",
    "https://api.example.onrender.com/api",
    "https://user:secret@api.example.onrender.com",
    "https://api.example.onrender.com?x=1",
    "not a url",
  ])("refuses %s as the API address", (address) => {
    expect(() => vercelConfig(address)).toThrow();
  });
});
