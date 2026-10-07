const request = require("supertest");

const { SIGN_IN_FIRST_MESSAGE } = require("../src/authentication");
const { WRONG_ORIGIN_MESSAGE } = require("../src/sameOrigin");
const { SESSION_LIFETIME_SECONDS } = require("../src/sessions");
const { USER_DATA_COLLECTIONS } = require("../src/users");
const { HUBOT, OCTOCAT } = require("./support/userStoreContract");
const { TEST_APP_ORIGIN, buildTestApp } = require("./support/testApp");

let app;
let clock;
let sessions;
let users;

beforeEach(() => {
  ({ app, clock, sessions, users } = buildTestApp());
});

async function signIn(profile = OCTOCAT) {
  const user = await users.saveSignedInUser(profile, new Date(clock.now()));
  const token = await sessions.issue(user);
  return { user, cookie: `session=${token}` };
}

function clearedSessionCookie(response) {
  const cookies = response.headers["set-cookie"] ?? [];
  return cookies.find((cookie) => cookie.startsWith("session=;"));
}

describe("GET /api/me", () => {
  test("a signed-in user gets their public profile and nothing else", async () => {
    const { user, cookie } = await signIn();

    const response = await request(app).get("/api/me").set("Cookie", cookie);

    expect(response.status).toBe(200);
    expect(response.body).toEqual({
      id: user.id,
      login: OCTOCAT.login,
      name: OCTOCAT.name,
      avatar_url: OCTOCAT.avatarUrl,
    });
  });

  test("no cookie is a 401 that clears nothing", async () => {
    const response = await request(app).get("/api/me");

    expect(response.status).toBe(401);
    expect(response.body).toEqual({ error: SIGN_IN_FIRST_MESSAGE });
    expect(response.headers["set-cookie"]).toBeUndefined();
  });

  test("a forged token is a 401 and the cookie is cleared", async () => {
    const response = await request(app).get("/api/me").set("Cookie", "session=forged.token.here");

    expect(response.status).toBe(401);
    expect(clearedSessionCookie(response)).toBeDefined();
  });

  test("an expired token is a 401", async () => {
    const { cookie } = await signIn();
    clock.advanceSeconds(SESSION_LIFETIME_SECONDS);

    const response = await request(app).get("/api/me").set("Cookie", cookie);

    expect(response.status).toBe(401);
  });

  test("a token for a deleted account is a 401", async () => {
    const { user, cookie } = await signIn();
    await users.deleteAccount(user.id);

    const response = await request(app).get("/api/me").set("Cookie", cookie);

    expect(response.status).toBe(401);
  });

  test("the cleared cookie carries the same path and protections as the session cookie", async () => {
    const response = await request(app).get("/api/me").set("Cookie", "session=forged");

    const cleared = clearedSessionCookie(response);
    expect(cleared).toContain("Path=/api");
    expect(cleared).toContain("HttpOnly");
    expect(cleared).toContain("SameSite=Strict");
    expect(cleared).toMatch(/Expires=Thu, 01 Jan 1970/);
  });
});

describe("POST /api/auth/logout", () => {
  test("signing out cancels the token on every device", async () => {
    const { user, cookie } = await signIn();
    const otherDevice = `session=${await sessions.issue(user)}`;

    const response = await request(app)
      .post("/api/auth/logout")
      .set("Origin", TEST_APP_ORIGIN)
      .set("Cookie", cookie);

    expect(response.status).toBe(204);
    expect(clearedSessionCookie(response)).toBeDefined();
    for (const anyCookie of [cookie, otherDevice]) {
      const after = await request(app).get("/api/me").set("Cookie", anyCookie);
      expect(after.status).toBe(401);
    }
  });

  test("signing in again after signing out works", async () => {
    const { cookie } = await signIn();
    await request(app)
      .post("/api/auth/logout")
      .set("Origin", TEST_APP_ORIGIN)
      .set("Cookie", cookie);

    const { cookie: newCookie } = await signIn();
    const response = await request(app).get("/api/me").set("Cookie", newCookie);

    expect(response.status).toBe(200);
  });

  test("signing out leaves other users signed in", async () => {
    const { cookie } = await signIn(OCTOCAT);
    const { cookie: hubotCookie } = await signIn(HUBOT);

    await request(app)
      .post("/api/auth/logout")
      .set("Origin", TEST_APP_ORIGIN)
      .set("Cookie", cookie);

    const response = await request(app).get("/api/me").set("Cookie", hubotCookie);
    expect(response.status).toBe(200);
  });

  test("signing out with no session is a harmless 204", async () => {
    const response = await request(app).post("/api/auth/logout").set("Origin", TEST_APP_ORIGIN);

    expect(response.status).toBe(204);
    expect(clearedSessionCookie(response)).toBeDefined();
  });
});

describe("DELETE /api/me", () => {
  test("deletes the user and their records in every user-data collection, then clears the cookie", async () => {
    const { user, cookie } = await signIn(OCTOCAT);
    const { user: hubot, cookie: hubotCookie } = await signIn(HUBOT);
    for (const collectionName of USER_DATA_COLLECTIONS) {
      users.addUserData(collectionName, user.id);
      users.addUserData(collectionName, hubot.id);
    }

    const response = await request(app)
      .delete("/api/me")
      .set("Origin", TEST_APP_ORIGIN)
      .set("Cookie", cookie);

    expect(response.status).toBe(204);
    expect(clearedSessionCookie(response)).toBeDefined();
    expect(await users.findById(user.id)).toBeNull();
    for (const collectionName of USER_DATA_COLLECTIONS) {
      expect(users.countUserData(collectionName, user.id)).toBe(0);
      expect(users.countUserData(collectionName, hubot.id)).toBe(1);
    }
    const hubotAfter = await request(app).get("/api/me").set("Cookie", hubotCookie);
    expect(hubotAfter.status).toBe(200);
  });

  test("without a session it is a 401 and deletes nothing", async () => {
    const { user } = await signIn();

    const response = await request(app).delete("/api/me").set("Origin", TEST_APP_ORIGIN);

    expect(response.status).toBe(401);
    expect(await users.findById(user.id)).not.toBeNull();
  });
});

describe("requests that change something must come from the site", () => {
  test.each([
    ["no Origin header", undefined],
    ["another site", "https://attacker.example"],
    ["the site on another port", "http://localhost:3000"],
    ["the site over https", "https://localhost:5173"],
  ])("%s is refused with 403 before the route runs", async (description, origin) => {
    const { user, cookie } = await signIn();
    let attempt = request(app).delete("/api/me").set("Cookie", cookie);
    if (origin !== undefined) {
      attempt = attempt.set("Origin", origin);
    }

    const response = await attempt;

    expect(response.status).toBe(403);
    expect(response.body).toEqual({ error: WRONG_ORIGIN_MESSAGE });
    expect(await users.findById(user.id)).not.toBeNull();
  });

  test("reads need no Origin header", async () => {
    const { cookie } = await signIn();

    const response = await request(app).get("/api/me").set("Cookie", cookie);

    expect(response.status).toBe(200);
  });
});
