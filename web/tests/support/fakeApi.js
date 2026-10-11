import { vi } from "vitest";

export function jsonReply(status, body, headers = {}) {
  return { status, body, headers };
}

export function emptyReply(status) {
  return { status, body: undefined, headers: {} };
}

export function installFakeApi(routes) {
  const calls = [];
  const fetchMock = vi.fn(async (path, options = {}) => {
    const method = options.method ?? "GET";
    const key = `${method} ${path}`;
    calls.push({ key, options });
    const route = routes[key];
    if (route === undefined) {
      throw new TypeError(`No fake reply for ${key}`);
    }
    const reply = typeof route === "function" ? await route(options) : route;
    return toResponse(reply);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

function toResponse({ status, body, headers }) {
  if (body === undefined) {
    return new Response(null, { status, headers });
  }
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return new Response(text, {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}
