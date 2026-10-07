/**
 * Checks on request bodies, mirroring the retrieval service's rules and messages
 * (`retrieval/src/retrieval/service/request_fields.py`), so a person sees the same message whether
 * Node or the service refuses a field. Messages name the field and the rule, never the value sent.
 *
 * A body must be a JSON object holding every required field and no field outside the required and
 * optional ones. Text is trimmed and must be non-blank and at most 1,000 characters.
 */

const { ApiError } = require("./apiError");

const BAD_REQUEST_STATUS = 400;

const MAX_TEXT_CHARACTERS = 1000;

/**
 * @param {unknown} body
 * @param {string[]} requiredFields
 * @param {string[]} [optionalFields]
 * @returns {Record<string, unknown>}
 */
function requireFields(body, requiredFields, optionalFields = []) {
  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    throw badRequest("The body must be a JSON object");
  }
  const allowed = new Set([...requiredFields, ...optionalFields]);
  const unknown = Object.keys(body)
    .filter((fieldName) => !allowed.has(fieldName))
    .sort();
  if (unknown.length > 0) {
    throw badRequest(`Unknown fields: ${unknown.join(", ")}`);
  }
  const missing = requiredFields.filter((fieldName) => !(fieldName in body));
  if (missing.length > 0) {
    throw badRequest(`Missing fields: ${missing.join(", ")}`);
  }
  return body;
}

/**
 * @param {unknown} value
 * @param {string} fieldName
 * @returns {string} the text, trimmed
 */
function textField(value, fieldName) {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw badRequest(`${fieldName} must be a non-blank string`);
  }
  const text = value.trim();
  if (text.length > MAX_TEXT_CHARACTERS) {
    throw badRequest(`${fieldName} must be at most ${MAX_TEXT_CHARACTERS} characters`);
  }
  return text;
}

/**
 * @param {unknown} value
 * @param {string} fieldName
 * @returns {boolean}
 */
function booleanField(value, fieldName) {
  if (typeof value !== "boolean") {
    throw badRequest(`${fieldName} must be true or false`);
  }
  return value;
}

/**
 * @param {unknown} value
 * @param {string} fieldName
 * @param {number} lowest
 * @param {number} highest
 * @returns {number}
 */
function wholeNumberField(value, fieldName, lowest, highest) {
  if (!Number.isInteger(value) || value < lowest || value > highest) {
    throw badRequest(`${fieldName} must be a whole number from ${lowest} to ${highest}`);
  }
  return value;
}

/** @param {string} message */
function badRequest(message) {
  return new ApiError(BAD_REQUEST_STATUS, message);
}

module.exports = {
  MAX_TEXT_CHARACTERS,
  badRequest,
  booleanField,
  requireFields,
  textField,
  wholeNumberField,
};
