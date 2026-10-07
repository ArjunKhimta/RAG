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
 */

const express = require("express");
const helmet = require("helmet");

const { registerErrorReplies } = require("./errors");

const MAX_REQUEST_BYTES = 16 * 1024;

/** @returns {import("express").Express} */
function createApp() {
  const app = express();
  app.use(helmet());
  app.use(express.json({ limit: MAX_REQUEST_BYTES }));
  app.get("/health", reportHealth);
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

module.exports = { MAX_REQUEST_BYTES, createApp };
