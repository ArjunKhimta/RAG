const { createApp } = require("../../src/app");
const { createSessions } = require("../../src/sessions");
const { createSignInAttempts } = require("../../src/signInAttempts");
const { createFakeGitHub } = require("./fakeGitHub");
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

/** The whole app over an in-memory users store, a fake GitHub, and a hand-moved clock. */
function buildTestApp() {
  const clock = createClock();
  const users = createMemoryUserStore();
  const github = createFakeGitHub();
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
    now: clock.now,
  });
  return { app, clock, github, signInAttempts, sessions, users };
}

module.exports = {
  START_TIME,
  TEST_APP_ORIGIN,
  TEST_SESSION_SECRET,
  buildTestApp,
  createClock,
};
