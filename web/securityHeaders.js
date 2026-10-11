export const API_PROXY_TARGET = "http://127.0.0.1:3000";

export const GITHUB_AVATAR_ORIGIN = "https://avatars.githubusercontent.com";

export const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  `img-src 'self' blob: ${GITHUB_AVATAR_ORIGIN}`,
  "connect-src 'self'",
  "font-src 'self'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

export const SHARED_SECURITY_HEADERS = {
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
};

export const BUILT_SITE_HEADERS = {
  ...SHARED_SECURITY_HEADERS,
  "Content-Security-Policy": CONTENT_SECURITY_POLICY,
};
