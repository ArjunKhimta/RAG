/**
 * A sign-in attempt: what the API must remember between sending someone to GitHub and GitHub
 * sending them back.
 *
 * Two random values are made for each attempt:
 * - `state`, which GitHub hands back unchanged; the callback refuses a `state` that does not match,
 *   so a sign-in link made on another site cannot complete a sign-in in this browser
 * - the PKCE `code_verifier`, whose SHA-256 (the `code_challenge`) goes to GitHub at the start and
 *   which itself is sent only when trading the code, so a code intercepted on the way back is
 *   useless without it
 *
 * Both travel in a signed token in a cookie that lasts 10 minutes (GitHub's codes last as long),
 * is limited to the sign-in paths, and is cleared at the callback. It is `SameSite=Lax`, not
 * `Strict`, because the callback is a navigation arriving from github.com, and browsers send only
 * `Lax` cookies on those.
 */

const crypto = require("node:crypto");

const { createTokenSigner } = require("./signedTokens");

const SIGN_IN_ATTEMPT_COOKIE_NAME = "sign_in_attempt";

const SIGN_IN_ATTEMPT_LIFETIME_SECONDS = 10 * 60;

const SIGN_IN_ATTEMPT_COOKIE_PATH = "/api/auth/github";

const SIGN_IN_ATTEMPT_AUDIENCE = "repositorycite-sign-in";

const RANDOM_BYTE_COUNT = 32;

const RANDOM_VALUE_PATTERN = /^[A-Za-z0-9_-]{43}$/;

/**
 * @param {{ sessionSecret: string, secureCookies: boolean, now?: () => number }} options
 */
function createSignInAttempts({ sessionSecret, secureCookies, now = Date.now }) {
  const signer = createTokenSigner({ secret: sessionSecret, now });
  const cookieOptions = {
    httpOnly: true,
    secure: secureCookies,
    sameSite: "lax",
    path: SIGN_IN_ATTEMPT_COOKIE_PATH,
  };

  return {
    /**
     * Make a new attempt and the token that carries it.
     * @returns {Promise<{ state: string, codeVerifier: string, codeChallenge: string, token: string }>}
     */
    async start() {
      const state = randomValue();
      const codeVerifier = randomValue();
      const token = await signer.sign(
        { state, code_verifier: codeVerifier },
        { audience: SIGN_IN_ATTEMPT_AUDIENCE, lifetimeSeconds: SIGN_IN_ATTEMPT_LIFETIME_SECONDS },
      );
      return { state, codeVerifier, codeChallenge: codeChallengeFor(codeVerifier), token };
    },

    /**
     * The attempt a token carries, or null for one that is forged, expired, or malformed.
     * @param {string} token
     * @returns {Promise<{ state: string, codeVerifier: string } | null>}
     */
    async read(token) {
      const payload = await signer.verify(token, SIGN_IN_ATTEMPT_AUDIENCE);
      if (payload === null) {
        return null;
      }
      const { state, code_verifier: codeVerifier } = payload;
      if (!isRandomValue(state) || !isRandomValue(codeVerifier)) {
        return null;
      }
      return { state, codeVerifier };
    },

    /**
     * @param {import("express").Response} response
     * @param {string} token
     */
    setCookie(response, token) {
      response.cookie(SIGN_IN_ATTEMPT_COOKIE_NAME, token, {
        ...cookieOptions,
        maxAge: SIGN_IN_ATTEMPT_LIFETIME_SECONDS * 1000,
      });
    },

    /** @param {import("express").Response} response */
    clearCookie(response) {
      response.clearCookie(SIGN_IN_ATTEMPT_COOKIE_NAME, cookieOptions);
    },
  };
}

/** 32 random bytes as base64url: 43 characters, the shortest `code_verifier` PKCE allows. */
function randomValue() {
  return crypto.randomBytes(RANDOM_BYTE_COUNT).toString("base64url");
}

/** @param {string} codeVerifier */
function codeChallengeFor(codeVerifier) {
  return crypto.createHash("sha256").update(codeVerifier).digest("base64url");
}

/** @param {unknown} value */
function isRandomValue(value) {
  return typeof value === "string" && RANDOM_VALUE_PATTERN.test(value);
}

/**
 * Compare two values in time that does not depend on where they differ.
 * @param {string} expected
 * @param {unknown} received
 */
function sameValue(expected, received) {
  if (typeof received !== "string") {
    return false;
  }
  const expectedBytes = Buffer.from(expected);
  const receivedBytes = Buffer.from(received);
  return (
    expectedBytes.length === receivedBytes.length &&
    crypto.timingSafeEqual(expectedBytes, receivedBytes)
  );
}

module.exports = {
  SIGN_IN_ATTEMPT_AUDIENCE,
  SIGN_IN_ATTEMPT_COOKIE_NAME,
  SIGN_IN_ATTEMPT_COOKIE_PATH,
  SIGN_IN_ATTEMPT_LIFETIME_SECONDS,
  codeChallengeFor,
  createSignInAttempts,
  sameValue,
};
