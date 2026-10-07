const { SignJWT, UnsecuredJWT } = require("jose");

const {
  SESSION_AUDIENCE,
  SESSION_LIFETIME_SECONDS,
  TOKEN_ISSUER,
  createSessions,
} = require("../src/sessions");
const { TEST_SESSION_SECRET, createClock } = require("./support/testApp");

const USER = { id: "0123456789abcdef01234567", sessionVersion: 3 };

let clock;
let sessions;

beforeEach(() => {
  clock = createClock();
  sessions = createSessions({
    sessionSecret: TEST_SESSION_SECRET,
    secureCookies: false,
    now: clock.now,
  });
});

function signWith(secret, claims, { audience = SESSION_AUDIENCE, subject = USER.id } = {}) {
  const issuedAtSeconds = Math.floor(clock.now() / 1000);
  return new SignJWT(claims)
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(subject)
    .setIssuer(TOKEN_ISSUER)
    .setAudience(audience)
    .setIssuedAt(issuedAtSeconds)
    .setExpirationTime(issuedAtSeconds + 60)
    .sign(new TextEncoder().encode(secret));
}

test("a token reads back as the user and session version it was issued for", async () => {
  const token = await sessions.issue(USER);

  expect(await sessions.read(token)).toEqual({ userId: USER.id, sessionVersion: 3 });
});

test("a token works until the end of its 7 days and not a second after", async () => {
  const token = await sessions.issue(USER);

  clock.advanceSeconds(SESSION_LIFETIME_SECONDS - 1);
  expect(await sessions.read(token)).not.toBeNull();

  clock.advanceSeconds(1);
  expect(await sessions.read(token)).toBeNull();
});

test("a token with any character changed is refused", async () => {
  const token = await sessions.issue(USER);
  const [header, payload, signature] = token.split(".");
  const changedPayload = Buffer.from(
    JSON.stringify({ ...decode(payload), sub: "fedcba9876543210fedcba98" }),
  ).toString("base64url");
  const changedSignature = (signature.startsWith("A") ? "B" : "A") + signature.slice(1);

  expect(await sessions.read(`${header}.${changedPayload}.${signature}`)).toBeNull();
  expect(await sessions.read(`${header}.${payload}.${changedSignature}`)).toBeNull();
});

test("a token signed with another secret is refused", async () => {
  const token = await signWith("another-secret-".padEnd(48, "y"), { session_version: 3 });

  expect(await sessions.read(token)).toBeNull();
});

test("an unsigned token is refused", async () => {
  const token = new UnsecuredJWT({ session_version: 3 })
    .setSubject(USER.id)
    .setIssuer(TOKEN_ISSUER)
    .setAudience(SESSION_AUDIENCE)
    .setExpirationTime("1h")
    .encode();

  expect(await sessions.read(token)).toBeNull();
});

test("a token signed with our secret for another audience is refused", async () => {
  const token = await signWith(
    TEST_SESSION_SECRET,
    { session_version: 3 },
    { audience: "repositorycite-sign-in" },
  );

  expect(await sessions.read(token)).toBeNull();
});

test.each([
  ["a user ID that is not an ObjectId", { session_version: 3 }, "not-an-object-id"],
  ["no session version", {}, USER.id],
  ["a session version of 0", { session_version: 0 }, USER.id],
  ["a session version that is text", { session_version: "3" }, USER.id],
])("a correctly signed token with %s is refused", async (description, claims, subject) => {
  const token = await signWith(TEST_SESSION_SECRET, claims, { subject });

  expect(await sessions.read(token)).toBeNull();
});

test.each(["", "not-a-token", "a.b.c"])("text %p is not a token", async (text) => {
  expect(await sessions.read(text)).toBeNull();
});

test("the token holds only the user ID, session version, and the standard claims", async () => {
  const token = await sessions.issue(USER);

  expect(Object.keys(decode(token.split(".")[1])).sort()).toEqual([
    "aud",
    "exp",
    "iat",
    "iss",
    "session_version",
    "sub",
  ]);
});

function decode(part) {
  return JSON.parse(Buffer.from(part, "base64url").toString("utf8"));
}
