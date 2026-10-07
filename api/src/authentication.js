/**
 * Finding out who sent a request.
 *
 * A request is signed in when its session cookie holds a token this API signed, that has not
 * expired, for a user who still exists, with the `session_version` the user record holds now. Any
 * other token (forged, expired, for a deleted account, or issued before the user signed out) is
 * treated as no session at all, and the cookie is cleared so the browser stops sending it.
 */

const { readCookie } = require("./cookies");
const { SESSION_COOKIE_NAME } = require("./sessions");

const UNAUTHORIZED_STATUS = 401;

const SIGN_IN_FIRST_MESSAGE = "Sign in first";

/**
 * @param {{
 *   sessions: ReturnType<typeof import("./sessions").createSessions>,
 *   users: { findById(userId: string): Promise<import("./users").User | null> },
 * }} dependencies
 */
function createAuthentication({ sessions, users }) {
  /**
   * Return the signed-in user, or null, clearing a cookie that no longer stands for a session.
   * @param {import("express").Request} request
   * @param {import("express").Response} response
   */
  async function findSignedInUser(request, response) {
    const token = readCookie(request, SESSION_COOKIE_NAME);
    if (token === null) {
      return null;
    }
    const user = await userForToken(token);
    if (user === null) {
      sessions.clearCookie(response);
    }
    return user;
  }

  /** @param {string} token */
  async function userForToken(token) {
    const claims = await sessions.read(token);
    if (claims === null) {
      return null;
    }
    const user = await users.findById(claims.userId);
    if (user === null || user.sessionVersion !== claims.sessionVersion) {
      return null;
    }
    return user;
  }

  /**
   * Route guard: continue with `request.user` set, or reply 401.
   * @param {import("express").Request} request
   * @param {import("express").Response} response
   * @param {import("express").NextFunction} next
   */
  async function requireUser(request, response, next) {
    const user = await findSignedInUser(request, response);
    if (user === null) {
      response.status(UNAUTHORIZED_STATUS).json({ error: SIGN_IN_FIRST_MESSAGE });
      return;
    }
    request.user = user;
    next();
  }

  return { findSignedInUser, requireUser };
}

module.exports = { SIGN_IN_FIRST_MESSAGE, createAuthentication };
