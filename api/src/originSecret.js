/**
 * Answer the API only for requests that came through the website.
 *
 * In production the site is served by Vercel, which forwards `/api/...` to this API on Render.
 * Render also gives the API a public address of its own, and a request sent there directly would
 * skip Vercel, so its `X-Forwarded-For` could name any address and the per-address sign-in limit
 * could be dodged. Vercel therefore adds a secret header, `X-Origin-Secret`, to every request it
 * forwards (after deleting any the visitor sent), and every `/api` request without the right value
 * is refused with 403. The comparison takes the same time however much of a guess is right.
 *
 * Locally there is no Vercel, so the check is off unless `ORIGIN_SECRET` is set; the settings
 * require it whenever `APP_ORIGIN` uses https, so production cannot run without it.
 */

const crypto = require("node:crypto");

const ORIGIN_SECRET_HEADER = "X-Origin-Secret";

const FORBIDDEN_STATUS = 403;

const NOT_THROUGH_SITE_MESSAGE = "Requests to the API must come through the RepositoryCite site";

/** @param {string} originSecret */
function requireOriginSecret(originSecret) {
  const expected = digest(originSecret);
  /**
   * @param {import("express").Request} request
   * @param {import("express").Response} response
   * @param {import("express").NextFunction} next
   */
  return function checkOriginSecret(request, response, next) {
    const received = request.get(ORIGIN_SECRET_HEADER);
    if (received !== undefined && crypto.timingSafeEqual(digest(received), expected)) {
      next();
      return;
    }
    response.status(FORBIDDEN_STATUS).json({ error: NOT_THROUGH_SITE_MESSAGE });
  };
}

/** Hash both sides first, so values of different lengths compare in the same time. */
function digest(text) {
  return crypto.createHash("sha256").update(text).digest();
}

module.exports = { NOT_THROUGH_SITE_MESSAGE, ORIGIN_SECRET_HEADER, requireOriginSecret };
