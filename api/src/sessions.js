/**
 * Sessions: who is signed in, carried by the browser as a signed token in a cookie.
 *
 * After GitHub confirms who someone is, the API gives the browser a JSON Web Token (JWT) signed
 * with `SESSION_SECRET` (HMAC SHA-256). It holds only the user's ID and their `session_version`,
 * and expires after 7 days. Anyone can read a JWT, but only the holder of the secret can make one
 * the API accepts, so the browser cannot change whose session it is.
 *
 * A signed token cannot be taken back once issued, so every request also compares the token's
 * `session_version` with the user record: signing out raises the number in the record, and every
 * token issued before that stops working at once, on every device.
 *
 * The cookie is `HttpOnly` (page scripts cannot read it, so injected script cannot steal it),
 * `SameSite=Strict` (the browser sends it only on requests from our own pages), limited to `/api`,
 * and `Secure` (https only) whenever the site itself uses https; browsers refuse `Secure` cookies
 * on plain-http localhost, so a local run leaves it off.
 */

const { SignJWT, jwtVerify } = require("jose");

const SESSION_COOKIE_NAME = "session";

const SESSION_LIFETIME_SECONDS = 7 * 24 * 60 * 60;

const SESSION_COOKIE_PATH = "/api";

const TOKEN_ISSUER = "repositorycite-api";

const SESSION_AUDIENCE = "repositorycite-session";

const SIGNING_ALGORITHM = "HS256";

const USER_ID_PATTERN = /^[0-9a-f]{24}$/;

/**
 * @param {{ sessionSecret: string, secureCookies: boolean, now?: () => number }} options
 */
function createSessions({ sessionSecret, secureCookies, now = Date.now }) {
  const signingKey = new TextEncoder().encode(sessionSecret);
  const cookieOptions = {
    httpOnly: true,
    secure: secureCookies,
    sameSite: "strict",
    path: SESSION_COOKIE_PATH,
  };

  return {
    /**
     * Make a session token for `user`.
     * @param {{ id: string, sessionVersion: number }} user
     * @returns {Promise<string>}
     */
    issue(user) {
      const issuedAtSeconds = Math.floor(now() / 1000);
      return new SignJWT({ session_version: user.sessionVersion })
        .setProtectedHeader({ alg: SIGNING_ALGORITHM })
        .setSubject(user.id)
        .setIssuer(TOKEN_ISSUER)
        .setAudience(SESSION_AUDIENCE)
        .setIssuedAt(issuedAtSeconds)
        .setExpirationTime(issuedAtSeconds + SESSION_LIFETIME_SECONDS)
        .sign(signingKey);
    },

    /**
     * Return the user ID and session version a token carries, or null for a token that is
     * forged, altered, expired, meant for something else, or malformed.
     * @param {string} token
     * @returns {Promise<{ userId: string, sessionVersion: number } | null>}
     */
    async read(token) {
      let payload;
      try {
        const verified = await jwtVerify(token, signingKey, {
          algorithms: [SIGNING_ALGORITHM],
          issuer: TOKEN_ISSUER,
          audience: SESSION_AUDIENCE,
          currentDate: new Date(now()),
        });
        payload = verified.payload;
      } catch {
        return null;
      }
      return readClaims(payload);
    },

    /**
     * @param {import("express").Response} response
     * @param {string} token
     */
    setCookie(response, token) {
      response.cookie(SESSION_COOKIE_NAME, token, {
        ...cookieOptions,
        maxAge: SESSION_LIFETIME_SECONDS * 1000,
      });
    },

    /** @param {import("express").Response} response */
    clearCookie(response) {
      response.clearCookie(SESSION_COOKIE_NAME, cookieOptions);
    },
  };
}

/** @param {import("jose").JWTPayload} payload */
function readClaims(payload) {
  const userId = payload.sub;
  const sessionVersion = payload.session_version;
  if (typeof userId !== "string" || !USER_ID_PATTERN.test(userId)) {
    return null;
  }
  if (!Number.isInteger(sessionVersion) || sessionVersion < 1) {
    return null;
  }
  return { userId, sessionVersion };
}

module.exports = {
  SESSION_AUDIENCE,
  SESSION_COOKIE_NAME,
  SESSION_COOKIE_PATH,
  SESSION_LIFETIME_SECONDS,
  SIGNING_ALGORITHM,
  TOKEN_ISSUER,
  createSessions,
};
