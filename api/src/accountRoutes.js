/**
 * The signed-in person's own account:
 * - `GET /api/me`: who is signed in (login, name, avatar), or 401
 * - `POST /api/auth/logout`: sign out everywhere, by raising the user's `session_version` so every
 *   session token issued so far stops working; always 204, even with no session, so signing out
 *   twice is harmless
 * - `DELETE /api/me`: delete the account and everything the API stores about the person, then
 *   clear the cookie; the shared search indexes are untouched, since they belong to no one
 */

const express = require("express");

const NO_CONTENT_STATUS = 204;

/**
 * @param {{
 *   authentication: ReturnType<typeof import("./authentication").createAuthentication>,
 *   sessions: ReturnType<typeof import("./sessions").createSessions>,
 *   users: {
 *     endAllSessions(userId: string, now: Date): Promise<void>,
 *     deleteAccount(userId: string): Promise<void>,
 *   },
 *   now?: () => number,
 * }} dependencies
 */
function createAccountRouter({ authentication, sessions, users, now = Date.now }) {
  const router = express.Router();

  router.get("/me", authentication.requireUser, (request, response) => {
    response.json(publicProfile(request.user));
  });

  router.post("/auth/logout", async (request, response) => {
    const user = await authentication.findSignedInUser(request, response);
    if (user !== null) {
      await users.endAllSessions(user.id, new Date(now()));
    }
    sessions.clearCookie(response);
    response.status(NO_CONTENT_STATUS).end();
  });

  router.delete("/me", authentication.requireUser, async (request, response) => {
    await users.deleteAccount(request.user.id);
    sessions.clearCookie(response);
    response.status(NO_CONTENT_STATUS).end();
  });

  return router;
}

/** @param {import("./users").User} user */
function publicProfile(user) {
  return {
    id: user.id,
    login: user.login,
    name: user.name,
    avatar_url: user.avatarUrl,
  };
}

module.exports = { createAccountRouter };
