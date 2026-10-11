/**
 * The site's Vercel configuration, built from the same headers the preview server sends, so the
 * deployed site cannot drift from what was tested. `scripts/writeVercelConfig.js` writes it to
 * `vercel.json`, and a test fails if the committed file no longer matches.
 *
 * - `/api/...` is forwarded to the Node API on Render, so the browser sees one site: the session
 *   cookie stays first-party and `SameSite=Strict`, and the content security policy's
 *   `connect-src 'self'` holds.
 * - Any other path that is not a file the build produced gets `index.html`, so the site's own
 *   routes (such as `/repositories`) load on a refresh. Vercel serves real files first, and paths
 *   under `/assets/` are left out, so a missing script is a 404 rather than a page of HTML.
 * - Files under `/assets/` carry a hash of their content in their names, so they may be cached for
 *   a year without ever being checked again; a new build gives changed files new names.
 * - Install runs `npm ci --ignore-scripts`: exactly the lockfile, no install scripts.
 */

import { BUILT_SITE_HEADERS } from "./securityHeaders.js";

export const API_PATH_PATTERN = "/api/:path*";

export const PAGE_FALLBACK_PATTERN = "/((?!api/|assets/).*)";

export const SITE_HEADERS_PATTERN = "/((?!api/).*)";

export const ASSETS_PATTERN = "/assets/(.*)";

export const IMMUTABLE_CACHE = "public, max-age=31536000, immutable";

/**
 * @param {string} apiOrigin the Node API's https origin, such as https://example.onrender.com
 */
export function vercelConfig(apiOrigin) {
  return {
    $schema: "https://openapi.vercel.sh/vercel.json",
    framework: "vite",
    installCommand: "npm ci --ignore-scripts",
    buildCommand: "npm run build",
    outputDirectory: "dist",
    rewrites: [
      { source: API_PATH_PATTERN, destination: `${requireHttpsOrigin(apiOrigin)}${API_PATH_PATTERN}` },
      { source: PAGE_FALLBACK_PATTERN, destination: "/index.html" },
    ],
    headers: [
      {
        source: SITE_HEADERS_PATTERN,
        headers: Object.entries(BUILT_SITE_HEADERS).map(([key, value]) => ({ key, value })),
      },
      { source: ASSETS_PATTERN, headers: [{ key: "Cache-Control", value: IMMUTABLE_CACHE }] },
    ],
  };
}

/** The origin the committed configuration forwards `/api` to. */
export function apiOriginOf(config) {
  const apiRewrite = config.rewrites.find((rewrite) => rewrite.source === API_PATH_PATTERN);
  return apiRewrite.destination.slice(0, -API_PATH_PATTERN.length);
}

function requireHttpsOrigin(text) {
  const url = new URL(text);
  const isOriginOnly = url.pathname === "/" && !url.search && !url.hash && !url.username && !url.password;
  if (url.protocol !== "https:" || !isOriginOnly || text.replace(/\/$/, "") !== url.origin) {
    throw new Error("The API address must be an https origin only, such as https://example.onrender.com");
  }
  return url.origin;
}
