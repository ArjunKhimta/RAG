/**
 * Signing and checking the API's own JSON Web Tokens (HMAC SHA-256 with `SESSION_SECRET`).
 *
 * Two kinds exist, told apart by their audience: the session token (7 days) and the sign-in
 * attempt token (10 minutes, between leaving for GitHub and coming back). A token made for one is
 * refused as the other, so a sign-in attempt can never be used as a session.
 */

const { SignJWT, jwtVerify } = require("jose");

const TOKEN_ISSUER = "repositorycite-api";

const SIGNING_ALGORITHM = "HS256";

/** @param {{ secret: string, now?: () => number }} options */
function createTokenSigner({ secret, now = Date.now }) {
  const signingKey = new TextEncoder().encode(secret);

  return {
    /**
     * @param {Record<string, unknown>} claims
     * @param {{ audience: string, lifetimeSeconds: number, subject?: string }} options
     * @returns {Promise<string>}
     */
    sign(claims, { audience, lifetimeSeconds, subject }) {
      const issuedAtSeconds = Math.floor(now() / 1000);
      const token = new SignJWT(claims)
        .setProtectedHeader({ alg: SIGNING_ALGORITHM })
        .setIssuer(TOKEN_ISSUER)
        .setAudience(audience)
        .setIssuedAt(issuedAtSeconds)
        .setExpirationTime(issuedAtSeconds + lifetimeSeconds);
      if (subject !== undefined) {
        token.setSubject(subject);
      }
      return token.sign(signingKey);
    },

    /**
     * Return the claims of a token this API signed for `audience` that has not expired, or null.
     * @param {string} token
     * @param {string} audience
     * @returns {Promise<import("jose").JWTPayload | null>}
     */
    async verify(token, audience) {
      try {
        const verified = await jwtVerify(token, signingKey, {
          algorithms: [SIGNING_ALGORITHM],
          issuer: TOKEN_ISSUER,
          audience,
          currentDate: new Date(now()),
        });
        return verified.payload;
      } catch {
        return null;
      }
    },
  };
}

module.exports = { SIGNING_ALGORITHM, TOKEN_ISSUER, createTokenSigner };
