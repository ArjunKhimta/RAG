/**
 * A second guard against cross-site request forgery, behind the `SameSite=Strict` session cookie.
 *
 * Another website could try to make a visitor's browser send a request that changes something
 * (sign out, delete the account) to this API. Browsers attach an `Origin` header naming the page
 * that sent every such request, and a page cannot change it, so any request that is not a plain
 * read must name our own site (`APP_ORIGIN`) or it is refused with 403.
 */

const FORBIDDEN_STATUS = 403;

const READ_ONLY_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

const WRONG_ORIGIN_MESSAGE = "This request must come from the RepositoryCite site";

/** @param {string} appOrigin */
function requireSameOrigin(appOrigin) {
  /**
   * @param {import("express").Request} request
   * @param {import("express").Response} response
   * @param {import("express").NextFunction} next
   */
  return function checkOrigin(request, response, next) {
    if (READ_ONLY_METHODS.has(request.method) || request.get("Origin") === appOrigin) {
      next();
      return;
    }
    response.status(FORBIDDEN_STATUS).json({ error: WRONG_ORIGIN_MESSAGE });
  };
}

module.exports = { WRONG_ORIGIN_MESSAGE, requireSameOrigin };
