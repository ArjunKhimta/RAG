/**
 * The Node API: the only part of the system the browser talks to.
 *
 * Node owns everything about people (sign-in, who has added which repository, chat history, and
 * per-user limits) and forwards the retrieval work to the Flask service with a shared token. It
 * never reads the retrieval service's collections; it only calls the service over HTTP.
 *
 * Every reply is JSON, errors included. Request bodies are capped at 16 KB, matching the
 * retrieval service, so a huge body is refused before it is read. Security headers come from
 * Helmet, which also removes the header that would announce Express. The API sends only JSON,
 * never pages, so its Content-Security-Policy is the one for JSON APIs: nothing may load and no
 * page may frame a reply. (Helmet's page-oriented default also asks browsers to upgrade requests to
 * https, which broke plain-http local runs in Safari.)
 *
 * Everything the site calls lives under `/api`, so the same paths work through Vite's proxy in
 * development and Vercel's rewrite in production; `/health` stays at the root for the host's
 * health check. Under `/api`, any request that changes something must come from `APP_ORIGIN`.
 *
 * The site's page also calls `/health` directly, once, to wake the API when the host has put it
 * to sleep. Helmet tells browsers that only this API's own pages may load its replies
 * (`Cross-Origin-Resource-Policy: same-origin`), which would make that call show an error in the
 * console, so `/health` alone says any site may load it: its reply holds nothing but "ok".
 */

const express = require("express");
const helmet = require("helmet");

const { createAccountRouter } = require("./accountRoutes");
const { createAuthentication } = require("./authentication");
const { createConversationRouter } = require("./conversationRoutes");
const { createForwardingCheckRouter } = require("./forwardingCheck");
const { registerErrorReplies } = require("./errors");
const { createGitHubSignInRouter } = require("./githubSignInRoutes");
const { DEFAULT_LIMITS } = require("./limits");
const { requireOriginSecret } = require("./originSecret");
const { createRepositoryRouter } = require("./repositoryRoutes");
const { requireSameOrigin } = require("./sameOrigin");
const { createSlidingWindowLimiter } = require("./slidingWindowLimiter");
const { createDailyLimits } = require("./usage");

const MAX_REQUEST_BYTES = 16 * 1024;

const API_PATH = "/api";

const MINUTE_MILLISECONDS = 60 * 1000;

const JSON_API_CONTENT_SECURITY_POLICY = {
  useDefaults: false,
  directives: {
    defaultSrc: ["'none'"],
    frameAncestors: ["'none'"],
  },
};

/**
 * @param {{
 *   appOrigin: string,
 *   github: ReturnType<typeof import("./github").createGitHubClient>,
 *   signInAttempts: ReturnType<typeof import("./signInAttempts").createSignInAttempts>,
 *   sessions: ReturnType<typeof import("./sessions").createSessions>,
 *   users: ReturnType<typeof import("./users").createMongoUserStore>,
 *   retrievalService: ReturnType<typeof import("./retrievalService").createRetrievalService>,
 *   repositoryLinks: ReturnType<typeof import("./repositoryLinks").createMongoRepositoryLinkStore>,
 *   conversations: ReturnType<typeof import("./conversations").createMongoConversationStore>,
 *   usage: ReturnType<typeof import("./usage").createMongoUsageStore>,
 *   limits?: typeof DEFAULT_LIMITS,
 *   now?: () => number,
 * }} dependencies
 * @returns {import("express").Express}
 */
function createApp({
  appOrigin,
  github,
  signInAttempts,
  sessions,
  users,
  retrievalService,
  repositoryLinks,
  conversations,
  usage,
  limits = DEFAULT_LIMITS,
  now = Date.now,
  trustProxyHops = 0,
  forwardingCheck = false,
  originSecret = null,
}) {
  const authentication = createAuthentication({ sessions, users });
  const dailyLimits = createDailyLimits({ usage, limits, now });
  const questionsPerMinute = createSlidingWindowLimiter({
    limit: limits.questionsPerMinute,
    windowMilliseconds: MINUTE_MILLISECONDS,
    now,
  });
  const signInLimiter = createSlidingWindowLimiter({
    limit: limits.signInsPerWindow,
    windowMilliseconds: limits.signInWindowMinutes * MINUTE_MILLISECONDS,
    now,
  });
  const app = express();
  if (trustProxyHops > 0) {
    app.set("trust proxy", trustProxyHops);
  }
  app.use(helmet({ contentSecurityPolicy: JSON_API_CONTENT_SECURITY_POLICY }));
  app.get("/health", reportHealth);
  app.use(API_PATH, neverCache);
  if (originSecret !== null) {
    app.use(API_PATH, requireOriginSecret(originSecret));
  }
  app.use(express.json({ limit: MAX_REQUEST_BYTES }));
  app.use(API_PATH, requireSameOrigin(appOrigin));
  if (forwardingCheck) {
    app.use(API_PATH, createForwardingCheckRouter({ trustProxyHops }));
  }
  app.use(
    API_PATH,
    createGitHubSignInRouter({
      appOrigin,
      apiPath: API_PATH,
      github,
      signInAttempts,
      sessions,
      users,
      signInLimiter,
      now,
    }),
  );
  app.use(
    API_PATH,
    createAccountRouter({
      authentication,
      sessions,
      users,
      dailyLimits,
      repositoryLinks,
      limits,
      now,
    }),
  );
  app.use(
    `${API_PATH}/repositories`,
    createRepositoryRouter({
      retrievalService,
      repositoryLinks,
      dailyLimits,
      limits,
      requireUser: authentication.requireUser,
      now,
    }),
  );
  app.use(
    `${API_PATH}/conversations`,
    createConversationRouter({
      retrievalService,
      repositoryLinks,
      conversations,
      dailyLimits,
      questionsPerMinute,
      limits,
      requireUser: authentication.requireUser,
      now,
    }),
  );
  registerErrorReplies(app, MAX_REQUEST_BYTES);
  return app;
}

/**
 * @param {import("express").Request} request
 * @param {import("express").Response} response
 */
/**
 * Every API reply is about one person or one moment, so no browser or CDN may keep a copy: a
 * cached `/api/me` shown to the next visitor would be another person's account.
 */
function neverCache(request, response, next) {
  response.set("Cache-Control", "no-store");
  next();
}

function reportHealth(request, response) {
  response.set("Cross-Origin-Resource-Policy", "cross-origin");
  response.json({ status: "ok" });
}

module.exports = { API_PATH, MAX_REQUEST_BYTES, createApp };
