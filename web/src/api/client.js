const JSON_CONTENT_TYPE = "application/json";

const NO_CONTENT_STATUS = 204;

const UNREACHABLE_STATUS = 0;

export const UNREACHABLE_MESSAGE =
  "Could not reach the server. Check your connection and try again.";

export const UNEXPECTED_MESSAGE = "Something went wrong. Please try again.";

export class ApiRequestError extends Error {
  constructor(status, message, retryAfterSeconds) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export async function apiRequest(path, { method = "GET", body } = {}) {
  const response = await send(path, method, body);
  if (!response.ok) {
    throw await errorFrom(response);
  }
  if (response.status === NO_CONTENT_STATUS) {
    return null;
  }
  const reply = await readJson(response);
  if (reply === null) {
    throw new ApiRequestError(response.status, UNEXPECTED_MESSAGE, null);
  }
  return reply;
}

async function send(path, method, body) {
  const headers = { Accept: JSON_CONTENT_TYPE };
  const options = { method, headers, credentials: "same-origin" };
  if (body !== undefined) {
    headers["Content-Type"] = JSON_CONTENT_TYPE;
    options.body = JSON.stringify(body);
  }
  try {
    return await fetch(path, options);
  } catch {
    throw new ApiRequestError(UNREACHABLE_STATUS, UNREACHABLE_MESSAGE, null);
  }
}

async function errorFrom(response) {
  const reply = await readJson(response);
  const message = typeof reply?.error === "string" ? reply.error : UNEXPECTED_MESSAGE;
  return new ApiRequestError(response.status, message, retryAfterSeconds(response));
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function retryAfterSeconds(response) {
  const header = response.headers.get("Retry-After");
  if (header === null || !/^\d+$/.test(header.trim())) {
    return null;
  }
  return Number(header.trim());
}
