/**
 * An error a route can throw to give the caller a specific status and message, optionally with how
 * many seconds to wait before trying again. The error handler replies with exactly these, so the
 * message must already be safe to show: it never echoes what the caller sent.
 */
class ApiError extends Error {
  /**
   * @param {number} status the HTTP status to reply with
   * @param {string} message safe to show to the user
   * @param {number | null} [retryAfterSeconds]
   */
  constructor(status, message, retryAfterSeconds = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

module.exports = { ApiError };
