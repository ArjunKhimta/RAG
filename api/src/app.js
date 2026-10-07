/**
 * The Node API: the only part of the system the browser talks to.
 *
 * Node owns everything about people (sign-in, who has added which repository, chat history, and
 * per-user limits) and forwards the retrieval work to the Flask service with a shared token. It
 * never reads the retrieval service's collections; it only calls the service over HTTP.
 *
 * Every reply is JSON, errors included. Request bodies are capped at 16 KB, matching the
 * retrieval service, so a huge body is refused before it is read. Security headers come from
 * Helmet, which also removes the header that would announce Express.
 *
 * Everything the site calls lives under `/api`, so the same paths work through Vite's proxy in
 * development and Vercel's rewrite in production; `/health` stays at the root for the host's
 * health check. Under `/api`, any request that changes something must come from `APP_ORIGIN`.
 */

const express = require("express");
const helmet = require("helmet");

const { createAccountRouter } = require("./accountRoutes");
const { createAuthentication } = require("./authentication");
const { registerErrorReplies } = require("./errors");
const { requireSameOrigin } = require("./sameOrigin");

const MAX_REQUEST_BYTES = 16 * 1024;

const API_PATH = "/api";

/**
 * @param {{
 *   appOrigin: string,
 *   sessions: ReturnType<typeof import("./sessions").createSessions>,
 *   users: ReturnType<typeof import("./users").createMongoUserStore>,
 *   now?: () => number,
 * }} dependencies
 * @returns {import("express").Express}
 */
function createApp({ appOrigin, sessions, users, now = Date.now }) {
  const authentication = createAuthentication({ sessions, users });
  const app = express();
  app.use(helmet());
  app.use(express.json({ limit: MAX_REQUEST_BYTES }));
  app.get("/health", reportHealth);
  app.use(API_PATH, requireSameOrigin(appOrigin));
  app.use(API_PATH, createAccountRouter({ authentication, sessions, users, now }));
  registerErrorReplies(app, MAX_REQUEST_BYTES);
  return app;
}

/**
 * @param {import("express").Request} request
 * @param {import("express").Response} response
 */
function reportHealth(request, response) {
  response.json({ status: "ok" });
}

module.exports = { API_PATH, MAX_REQUEST_BYTES, createApp };
