/**
 * Reading cookies from a request. Express sets cookies itself (`response.cookie`) but does not read
 * them, and the one thing this API needs, finding a cookie by name, is a few lines, so there is no
 * cookie-parsing package.
 *
 * The browser sends `Cookie: name=value; other=value`. When a name appears twice (a cookie set on
 * two paths), the first one wins, as browsers list the most specific path first. A value that is
 * not valid percent-encoding is ignored rather than guessed at.
 */

/**
 * @param {import("express").Request} request
 * @param {string} cookieName
 * @returns {string | null}
 */
function readCookie(request, cookieName) {
  const header = request.get("Cookie");
  if (!header) {
    return null;
  }
  for (const pair of header.split(";")) {
    const separatorIndex = pair.indexOf("=");
    if (separatorIndex === -1) {
      continue;
    }
    const name = pair.slice(0, separatorIndex).trim();
    if (name === cookieName) {
      return decodeValue(pair.slice(separatorIndex + 1).trim());
    }
  }
  return null;
}

/** @param {string} value */
function decodeValue(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

module.exports = { readCookie };
