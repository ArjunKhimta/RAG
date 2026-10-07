/**
 * Scrubbing of secret material from any text that is about to be displayed or logged.
 *
 * Nothing in this API prints a credential on purpose. The realistic leak is indirect: a MongoDB
 * driver error can embed the full connection URI including the password, and an HTTP error from
 * GitHub or the retrieval service can echo what it was sent. Every logged string therefore passes
 * through `redact` first. Mirrors `retrieval/src/retrieval/redaction.py`.
 */

const REDACTION_PLACEHOLDER = "***REDACTED***";

const MINIMUM_SECRET_LENGTH = 8;

const URI_CREDENTIALS_PATTERN = /([a-zA-Z][a-zA-Z0-9+.-]*:\/\/)[^/\s@]+@/g;

const registeredSecrets = new Set();

/**
 * Record a value that must never appear in displayed text. Values shorter than
 * `MINIMUM_SECRET_LENGTH` are ignored, because replacing a short common string would corrupt
 * unrelated output without protecting anything worth protecting.
 * @param {string | undefined} value
 */
function registerSecret(value) {
  if (value && value.length >= MINIMUM_SECRET_LENGTH) {
    registeredSecrets.add(value);
  }
}

function clearRegisteredSecrets() {
  registeredSecrets.clear();
}

/**
 * Return `text` with every registered secret and every embedded URI credential removed.
 * @param {string} text
 * @returns {string}
 */
function redact(text) {
  const longestFirst = [...registeredSecrets].sort((first, second) => second.length - first.length);
  let redactedText = String(text);
  for (const secret of longestFirst) {
    redactedText = redactedText.split(secret).join(REDACTION_PLACEHOLDER);
  }
  return redactedText.replace(URI_CREDENTIALS_PATTERN, `$1${REDACTION_PLACEHOLDER}@`);
}

/**
 * Return a redacted description of `error`, with its stack when it has one, safe to log.
 * @param {unknown} error
 * @returns {string}
 */
function redactError(error) {
  if (error instanceof Error) {
    return redact(error.stack || `${error.name}: ${error.message}`);
  }
  return redact(String(error));
}

module.exports = {
  REDACTION_PLACEHOLDER,
  MINIMUM_SECRET_LENGTH,
  registerSecret,
  clearRegisteredSecrets,
  redact,
  redactError,
};
