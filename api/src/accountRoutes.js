/**
 * The signed-in person's own account:
 * - `GET /api/me`: who is signed in (login, name, avatar), or 401
 * - `GET /api/me/usage`: how much of today's allowance the person has used (questions, searches,
 *   new repositories to index) and when it resets, and how many repositories they have linked,
 *   so the site can show what is left before a limit is reached
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
 *   dailyLimits: ReturnType<typeof import("./usage").createDailyLimits>,
 *   repositoryLinks: { countForUser(userId: string): Promise<number> },
 *   limits: typeof import("./limits").DEFAULT_LIMITS,
 *   now?: () => number,
 * }} dependencies
 */
function createAccountRouter({
  authentication,
  sessions,
  users,
  dailyLimits,
  repositoryLinks,
  limits,
  now = Date.now,
}) {
  const router = express.Router();

  router.get("/me", authentication.requireUser, (request, response) => {
    response.json(publicProfile(request.user));
  });

  router.get("/me/usage", authentication.requireUser, async (request, response) => {
    const report = await dailyLimits.report(request.user.id);
    const linked = await repositoryLinks.countForUser(request.user.id);
    response.json({
      day: report.day,
      resets_at: report.resetsAt.toISOString(),
      ...report.counts,
      linked_repositories: { used: linked, limit: limits.linkedRepositories },
    });
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
