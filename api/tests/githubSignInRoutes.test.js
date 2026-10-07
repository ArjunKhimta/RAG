const crypto = require("node:crypto");
const request = require("supertest");

const { GitHubSignInError } = require("../src/github");
const { SIGN_IN_ATTEMPT_LIFETIME_SECONDS } = require("../src/signInAttempts");
const { FAKE_ACCESS_TOKEN, FAKE_AUTHORIZE_URL } = require("./support/fakeGitHub");
const { HUBOT, OCTOCAT } = require("./support/userStoreContract");
const { TEST_APP_ORIGIN, buildTestApp } = require("./support/testApp");

const CALLBACK_URL = `${TEST_APP_ORIGIN}/api/auth/github/callback`;

const SIGNED_IN_DESTINATION = `${TEST_APP_ORIGIN}/`;

let app;
let clock;
let github;
let users;
let logged;

beforeEach(() => {
  ({ app, clock, github, users } = buildTestApp());
  logged = [];
  jest.spyOn(console, "log").mockImplementation((line) => logged.push(line));
  jest.spyOn(console, "error").mockImplementation((line) => logged.push(line));
});

afterEach(() => {
  jest.restoreAllMocks();
});

function cookieNamed(response, name) {
  const cookies = response.headers["set-cookie"] ?? [];
  return cookies.find((cookie) => cookie.startsWith(`${name}=`));
}

function cookiePair(setCookie) {
  return setCookie.split(";")[0];
}

async function startSignIn() {
  const response = await request(app).get("/api/auth/github/login");
  const sentTo = new URL(response.headers.location);
  return {
    response,
    state: sentTo.searchParams.get("state"),
    codeChallenge: sentTo.searchParams.get("code_challenge"),
    attemptCookie: cookiePair(cookieNamed(response, "sign_in_attempt")),
  };
}

function callback(query, cookie) {
  const attempt = request(app).get("/api/auth/github/callback").query(query);
  return cookie === undefined ? attempt : attempt.set("Cookie", cookie);
}

function outcomeOf(response) {
  return new URL(response.headers.location).searchParams.get("sign_in");
}

describe("starting a sign-in", () => {
  test("sends the browser to GitHub with the callback, a state, and a PKCE challenge", async () => {
    const { response, state, codeChallenge } = await startSignIn();

    expect(response.status).toBe(302);
    expect(response.headers.location.startsWith(FAKE_AUTHORIZE_URL)).toBe(true);
    expect(response.headers["cache-control"]).toBe("no-store");
    expect(github.authorizations).toEqual([
      { state, codeChallenge, redirectUri: CALLBACK_URL },
    ]);
  });

  test("keeps the attempt in a 10-minute, HttpOnly, Lax cookie limited to the sign-in paths", async () => {
    const { response } = await startSignIn();

    const cookie = cookieNamed(response, "sign_in_attempt");
    expect(cookie).toContain(`Max-Age=${SIGN_IN_ATTEMPT_LIFETIME_SECONDS}`);
    expect(cookie).toContain("Path=/api/auth/github");
    expect(cookie).toContain("HttpOnly");
    expect(cookie).toContain("SameSite=Lax");
    expect(cookie).not.toContain("Secure");
  });

  test("needs no session and no Origin header", async () => {
    const response = await request(app).get("/api/auth/github/login");

    expect(response.status).toBe(302);
  });
});

describe("coming back from GitHub", () => {
  test("a matching attempt signs the person in and sends them to the site", async () => {
    const { state, codeChallenge, attemptCookie } = await startSignIn();

    const response = await callback({ code: "one-time-code", state }, attemptCookie);

    expect(response.status).toBe(302);
    expect(response.headers.location).toBe(SIGNED_IN_DESTINATION);
    const exchange = github.exchanges[0];
    expect(exchange.code).toBe("one-time-code");
    expect(exchange.redirectUri).toBe(CALLBACK_URL);
    expect(crypto.createHash("sha256").update(exchange.codeVerifier).digest("base64url")).toBe(
      codeChallenge,
    );
    expect(github.profileTokens).toEqual([FAKE_ACCESS_TOKEN]);

    const sessionCookie = cookieNamed(response, "session");
    expect(sessionCookie).toContain("HttpOnly");
    expect(sessionCookie).toContain("SameSite=Strict");
    expect(sessionCookie).toContain("Path=/api");
    const me = await request(app).get("/api/me").set("Cookie", cookiePair(sessionCookie));
    expect(me.status).toBe(200);
    expect(me.body.login).toBe(OCTOCAT.login);
  });

  test("the attempt cookie is cleared whatever the outcome", async () => {
    const { state, attemptCookie } = await startSignIn();

    const signedIn = await callback({ code: "c", state }, attemptCookie);
    const refused = await callback({ code: "c", state: "wrong" }, attemptCookie);

    for (const response of [signedIn, refused]) {
      const cleared = cookieNamed(response, "sign_in_attempt");
      expect(cleared).toMatch(/^sign_in_attempt=;/);
      expect(cleared).toContain("Path=/api/auth/github");
    }
  });

  test("the GitHub token never reaches the browser or the log", async () => {
    const { state, attemptCookie } = await startSignIn();

    const response = await callback({ code: "c", state }, attemptCookie);

    expect(JSON.stringify(response.headers)).not.toContain(FAKE_ACCESS_TOKEN);
    expect(logged.join("\n")).not.toContain(FAKE_ACCESS_TOKEN);
  });

  test("signing in again as the same person keeps one account", async () => {
    const first = await startSignIn();
    const firstReply = await callback({ code: "c1", state: first.state }, first.attemptCookie);
    const second = await startSignIn();
    const secondReply = await callback({ code: "c2", state: second.state }, second.attemptCookie);

    const firstMe = await request(app)
      .get("/api/me")
      .set("Cookie", cookiePair(cookieNamed(firstReply, "session")));
    const secondMe = await request(app)
      .get("/api/me")
      .set("Cookie", cookiePair(cookieNamed(secondReply, "session")));
    expect(secondMe.body.id).toBe(firstMe.body.id);
  });

  test("a different GitHub account gets its own account", async () => {
    github.profile = HUBOT;
    const { state, attemptCookie } = await startSignIn();

    const response = await callback({ code: "c", state }, attemptCookie);

    const me = await request(app)
      .get("/api/me")
      .set("Cookie", cookiePair(cookieNamed(response, "session")));
    expect(me.body).toMatchObject({ login: HUBOT.login, name: null });
  });

  test("declining on GitHub is 'denied' and calls GitHub no further", async () => {
    const { state, attemptCookie } = await startSignIn();

    const response = await callback({ error: "access_denied", state }, attemptCookie);

    expect(outcomeOf(response)).toBe("denied");
    expect(github.exchanges).toHaveLength(0);
    expect(cookieNamed(response, "session")).toBeUndefined();
  });

  test("another GitHub error is 'failed', logged by its code only", async () => {
    const { state, attemptCookie } = await startSignIn();

    const response = await callback(
      { error: "redirect_uri_mismatch", error_description: "<script>", state },
      attemptCookie,
    );

    expect(outcomeOf(response)).toBe("failed");
    expect(logged.join("\n")).toContain("redirect_uri_mismatch");
    expect(logged.join("\n")).not.toContain("<script>");
  });

  test("no attempt cookie is 'expired'", async () => {
    const { state } = await startSignIn();

    const response = await callback({ code: "c", state });

    expect(outcomeOf(response)).toBe("expired");
    expect(github.exchanges).toHaveLength(0);
  });

  test("an attempt older than 10 minutes is 'expired'", async () => {
    const { state, attemptCookie } = await startSignIn();
    clock.advanceSeconds(SIGN_IN_ATTEMPT_LIFETIME_SECONDS);

    const response = await callback({ code: "c", state }, attemptCookie);

    expect(outcomeOf(response)).toBe("expired");
  });

  test("a forged attempt cookie is 'expired'", async () => {
    const response = await callback({ code: "c", state: "s" }, "sign_in_attempt=forged");

    expect(outcomeOf(response)).toBe("expired");
  });

  test.each([
    ["another attempt's state", (mine, other) => ({ code: "c", state: other.state })],
    ["no state", () => ({ code: "c" })],
    ["the state given twice", (mine) => ({ code: "c", state: [mine.state, mine.state] })],
  ])("%s is 'failed' and calls GitHub no further", async (description, queryFor) => {
    const mine = await startSignIn();
    const other = await startSignIn();

    const response = await callback(queryFor(mine, other), mine.attemptCookie);

    expect(outcomeOf(response)).toBe("failed");
    expect(github.exchanges).toHaveLength(0);
    expect(cookieNamed(response, "session")).toBeUndefined();
  });

  test.each([
    ["no code", undefined],
    ["an empty code", ""],
    ["a code over 256 characters", "c".repeat(257)],
  ])("%s is 'failed'", async (description, code) => {
    const { state, attemptCookie } = await startSignIn();
    const query = code === undefined ? { state } : { code, state };

    const response = await callback(query, attemptCookie);

    expect(outcomeOf(response)).toBe("failed");
    expect(github.exchanges).toHaveLength(0);
  });

  test("a refused code exchange is 'failed', with the reason logged", async () => {
    github.exchangeError = new GitHubSignInError("token exchange refused (bad_verification_code)");
    const { state, attemptCookie } = await startSignIn();

    const response = await callback({ code: "c", state }, attemptCookie);

    expect(outcomeOf(response)).toBe("failed");
    expect(logged.join("\n")).toContain("bad_verification_code");
    expect(cookieNamed(response, "session")).toBeUndefined();
  });

  test("a failed profile lookup is 'failed' and saves no user", async () => {
    github.profileError = new GitHubSignInError("profile lookup failed with status 401");
    const { state, attemptCookie } = await startSignIn();

    const response = await callback({ code: "c", state }, attemptCookie);

    expect(outcomeOf(response)).toBe("failed");
    expect(cookieNamed(response, "session")).toBeUndefined();
  });

  test("an unexpected error, such as the database failing, is 'failed' and logged", async () => {
    users.saveSignedInUser = async () => {
      throw new Error("database unreachable");
    };
    const { state, attemptCookie } = await startSignIn();

    const response = await callback({ code: "c", state }, attemptCookie);

    expect(outcomeOf(response)).toBe("failed");
    expect(logged.join("\n")).toContain("database unreachable");
  });
});
