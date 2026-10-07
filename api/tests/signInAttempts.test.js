const crypto = require("node:crypto");

const { createSessions } = require("../src/sessions");
const {
  SIGN_IN_ATTEMPT_LIFETIME_SECONDS,
  createSignInAttempts,
  sameValue,
} = require("../src/signInAttempts");
const { TEST_SESSION_SECRET, createClock } = require("./support/testApp");

let clock;
let signInAttempts;

beforeEach(() => {
  clock = createClock();
  signInAttempts = createSignInAttempts({
    sessionSecret: TEST_SESSION_SECRET,
    secureCookies: false,
    now: clock.now,
  });
});

test("an attempt has a random state and verifier, and the challenge is the verifier's SHA-256", async () => {
  const attempt = await signInAttempts.start();

  expect(attempt.state).toMatch(/^[A-Za-z0-9_-]{43}$/);
  expect(attempt.codeVerifier).toMatch(/^[A-Za-z0-9_-]{43}$/);
  expect(attempt.state).not.toBe(attempt.codeVerifier);
  expect(attempt.codeChallenge).toBe(
    crypto.createHash("sha256").update(attempt.codeVerifier).digest("base64url"),
  );
});

test("two attempts share nothing", async () => {
  const first = await signInAttempts.start();
  const second = await signInAttempts.start();

  expect(second.state).not.toBe(first.state);
  expect(second.codeVerifier).not.toBe(first.codeVerifier);
});

test("an attempt reads back from its token for 10 minutes and not a second longer", async () => {
  const attempt = await signInAttempts.start();

  clock.advanceSeconds(SIGN_IN_ATTEMPT_LIFETIME_SECONDS - 1);
  expect(await signInAttempts.read(attempt.token)).toEqual({
    state: attempt.state,
    codeVerifier: attempt.codeVerifier,
  });

  clock.advanceSeconds(1);
  expect(await signInAttempts.read(attempt.token)).toBeNull();
});

test("a session token is not an attempt, and an attempt token is not a session", async () => {
  const sessions = createSessions({
    sessionSecret: TEST_SESSION_SECRET,
    secureCookies: false,
    now: clock.now,
  });
  const sessionToken = await sessions.issue({ id: "0123456789abcdef01234567", sessionVersion: 1 });
  const attempt = await signInAttempts.start();

  expect(await signInAttempts.read(sessionToken)).toBeNull();
  expect(await sessions.read(attempt.token)).toBeNull();
});

test.each(["", "forged", "a.b.c"])("text %p is not an attempt", async (text) => {
  expect(await signInAttempts.read(text)).toBeNull();
});

test.each([
  ["abc", "abc", true],
  ["abc", "abd", false],
  ["abc", "abcd", false],
  ["abc", "", false],
  ["abc", undefined, false],
  ["abc", ["abc"], false],
])("comparing %p with %p is %p", (expected, received, result) => {
  expect(sameValue(expected, received)).toBe(result);
});
