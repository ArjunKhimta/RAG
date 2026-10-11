const { createApp } = require("../../src/app");
const { withLimits } = require("../../src/limits");
const { createSessions } = require("../../src/sessions");
const { createSignInAttempts } = require("../../src/signInAttempts");
const { createFakeGitHub } = require("./fakeGitHub");
const { createMemoryConversationStore } = require("./memoryConversationStore");
const { createFakeRetrievalService } = require("./fakeRetrievalService");
const { createMemoryRepositoryLinkStore } = require("./memoryRepositoryLinkStore");
const { createMemoryUsageStore } = require("./memoryUsageStore");
const { createMemoryUserStore } = require("./memoryUserStore");

const TEST_APP_ORIGIN = "http://localhost:5173";

const TEST_SESSION_SECRET = "test-session-secret-".padEnd(48, "x");

const START_TIME = Date.parse("2026-10-07T12:00:00Z");

/** A clock the test moves by hand. */
function createClock(startMilliseconds = START_TIME) {
  let milliseconds = startMilliseconds;
  return {
    now: () => milliseconds,
    advanceSeconds(seconds) {
      milliseconds += seconds * 1000;
    },
  };
}

/**
 * The whole app over in-memory stores, a fake GitHub, a fake retrieval service, and a hand-moved
 * clock, with the real limits unless `limits` overrides some. `sessionCookieFor(profile)` signs
 * someone in directly and returns their cookie.
 * @param {{
 *   limits?: Partial<typeof import("../../src/limits").DEFAULT_LIMITS>,
 *   trustProxyHops?: number,
 *   forwardingCheck?: boolean,
 *   originSecret?: string | null,
 * }} [options]
 */
function buildTestApp({
  limits,
  trustProxyHops = 0,
  forwardingCheck = false,
  originSecret = null,
} = {}) {
  const clock = createClock();
  const users = createMemoryUserStore();
  const repositoryLinks = createMemoryRepositoryLinkStore();
  const conversations = createMemoryConversationStore();
  const usage = createMemoryUsageStore();
  const github = createFakeGitHub();
  const retrievalService = createFakeRetrievalService();
  const cookieSettings = {
    sessionSecret: TEST_SESSION_SECRET,
    secureCookies: false,
    now: clock.now,
  };
  const sessions = createSessions(cookieSettings);
  const signInAttempts = createSignInAttempts(cookieSettings);
  const app = createApp({
    appOrigin: TEST_APP_ORIGIN,
    github,
    signInAttempts,
    sessions,
    users,
    retrievalService,
    repositoryLinks,
    conversations,
    usage,
    limits: withLimits(limits),
    now: clock.now,
    trustProxyHops,
    forwardingCheck,
    originSecret,
  });

  async function sessionCookieFor(profile) {
    const user = await users.saveSignedInUser(profile, new Date(clock.now()));
    return `session=${await sessions.issue(user)}`;
  }

  return {
    app,
    clock,
    conversations,
    github,
    repositoryLinks,
    retrievalService,
    sessionCookieFor,
    sessions,
    signInAttempts,
    usage,
    users,
  };
}

module.exports = {
  START_TIME,
  TEST_APP_ORIGIN,
  TEST_SESSION_SECRET,
  buildTestApp,
  createClock,
};
