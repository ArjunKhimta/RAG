// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { BUILT_SITE_HEADERS } from "../securityHeaders.js";
import {
  API_PATH_PATTERN,
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
    const apiRewrite = committed.rewrites.find((rewrite) => rewrite.source === API_PATH_PATTERN);

    expect(apiRewrite.destination).toMatch(/^https:\/\/[a-z0-9.-]+\/api\/:path\*$/);
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
