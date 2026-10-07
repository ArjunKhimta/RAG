/**
 * The only way this API writes to its logs: every line passes through the redaction module, so a
 * secret that slipped into an error message is replaced before it reaches the terminal or Render's
 * log viewer.
 */

const { redact } = require("./redaction");

/** @param {string} message */
function logInfo(message) {
  console.log(redact(message));
}

/** @param {string} message */
function logError(message) {
  console.error(redact(message));
}

module.exports = { logInfo, logError };
