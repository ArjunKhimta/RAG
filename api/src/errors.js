/**
 * JSON error replies for every failure, mirroring the retrieval service: `{"error": "..."}` with
 * the HTTP status.
 *
 * A request the body reader refuses (too large, not valid JSON, an unsupported encoding) gets a
 * fixed message of our own, never the reader's text. A `RetrievalServiceError` already carries the
 * status, safe message, and any `Retry-After` the API should reply with, so routes let it through.
 * Any other error is unexpected: its details, which could hold a database address or a token, go
 * to the log only after redaction, and the caller sees a generic 500.
 */

const { logError } = require("./logging");
const { redactError } = require("./redaction");
const { RetrievalServiceError } = require("./retrievalService");

const NOT_FOUND_STATUS = 404;

const INTERNAL_ERROR_STATUS = 500;

const PAYLOAD_TOO_LARGE_STATUS = 413;

const NOT_FOUND_MESSAGE = "Not found";

const INTERNAL_ERROR_MESSAGE = "The API hit an unexpected error";

const UNREADABLE_REQUEST_MESSAGE = "The request could not be read";

const INVALID_JSON_MESSAGE = "The request body is not valid JSON";

const INVALID_JSON_ERROR_TYPE = "entity.parse.failed";

/**
 * Add the not-found reply and the error handler; call after every route is registered.
 * @param {import("express").Express} app
 * @param {number} maxRequestBytes
 */
function registerErrorReplies(app, maxRequestBytes) {
  app.use(replyNotFound);
  app.use(errorHandler(maxRequestBytes));
}

/**
 * @param {import("express").Request} request
 * @param {import("express").Response} response
 */
function replyNotFound(request, response) {
  response.status(NOT_FOUND_STATUS).json({ error: NOT_FOUND_MESSAGE });
}

/** @param {number} maxRequestBytes */
function errorHandler(maxRequestBytes) {
  const tooLargeMessage = `The request body is larger than ${maxRequestBytes / 1024} KB`;

  return function handleError(error, request, response, next) {
    if (response.headersSent) {
      next(error);
      return;
    }
    if (error instanceof RetrievalServiceError) {
      replyRetrievalServiceError(response, error);
      return;
    }
    if (isRefusedRequest(error)) {
      response.status(error.status).json({ error: refusalMessage(error, tooLargeMessage) });
      return;
    }
    logError(`Unexpected error on ${request.method} ${request.path}: ${redactError(error)}`);
    response.status(INTERNAL_ERROR_STATUS).json({ error: INTERNAL_ERROR_MESSAGE });
  };
}

/**
 * @param {import("express").Response} response
 * @param {RetrievalServiceError} error
 */
function replyRetrievalServiceError(response, error) {
  if (error.retryAfterSeconds !== null) {
    response.set("Retry-After", String(error.retryAfterSeconds));
  }
  response.status(error.status).json({ error: error.message });
}

/** @param {any} error */
function isRefusedRequest(error) {
  return (
    error !== null &&
    typeof error === "object" &&
    error.expose === true &&
    Number.isInteger(error.status) &&
    error.status >= 400 &&
    error.status < 500
  );
}

/**
 * @param {any} error
 * @param {string} tooLargeMessage
 */
function refusalMessage(error, tooLargeMessage) {
  if (error.status === PAYLOAD_TOO_LARGE_STATUS) {
    return tooLargeMessage;
  }
  if (error.type === INVALID_JSON_ERROR_TYPE) {
    return INVALID_JSON_MESSAGE;
  }
  return UNREADABLE_REQUEST_MESSAGE;
}

module.exports = {
  INTERNAL_ERROR_MESSAGE,
  INVALID_JSON_MESSAGE,
  NOT_FOUND_MESSAGE,
  UNREADABLE_REQUEST_MESSAGE,
  registerErrorReplies,
};
