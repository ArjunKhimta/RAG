/**
 * Signing in with GitHub:
 * - `GET /api/auth/github/login` starts an attempt (see `signInAttempts.js`), keeps it in a
 *   10-minute cookie, and sends the browser to GitHub's authorize page
 * - `GET /api/auth/github/callback` is where GitHub sends the browser back. It checks the attempt
 *   and its `state`, trades the code for a token, asks GitHub who the person is, saves or refreshes
 *   their user record, sets the session cookie, and sends the browser to the site
 *
 * Both are page visits rather than calls from a script, so the callback always answers with a
 * redirect to the site, never a JSON error: to `/` when signed in, otherwise to
 * `/?sign_in=denied` (the person said no on GitHub), `/?sign_in=expired` (no attempt cookie, as
 * after 10 minutes or in another browser), or `/?sign_in=failed` (anything else, with the reason
 * logged). The attempt cookie is cleared whatever the outcome, so it is used at most once.
 *
 * Opening the login link is limited to 20 times per 15 minutes per internet address (in memory);
 * past that it sends the browser to `/?sign_in=rate_limited` without contacting GitHub. Behind a
 * proxy (Vercel in production), Express must be told to trust the forwarded address, or every
 * person would share the proxy's address and its limit.
 */

const express = require("express");

const { logError, logInfo } = require("./logging");
const { readCookie } = require("./cookies");
const { GitHubSignInError, safeErrorCode } = require("./github");
const { redactError } = require("./redaction");
const { SIGN_IN_ATTEMPT_COOKIE_NAME, sameValue } = require("./signInAttempts");

const LOGIN_PATH = "/auth/github/login";

const CALLBACK_PATH = "/auth/github/callback";

const ACCESS_DENIED_ERROR = "access_denied";

const MAX_CODE_LENGTH = 256;

const SIGN_IN_OUTCOME_PARAMETER = "sign_in";

const SignInOutcome = Object.freeze({
  DENIED: "denied",
  EXPIRED: "expired",
  FAILED: "failed",
  RATE_LIMITED: "rate_limited",
});

const UNKNOWN_ADDRESS = "unknown";

/**
 * The address GitHub sends people back to; it must match the OAuth App's callback URL exactly.
 * @param {string} appOrigin
 * @param {string} apiPath
 */
function callbackUrlFor(appOrigin, apiPath) {
  return `${appOrigin}${apiPath}${CALLBACK_PATH}`;
}

/**
 * @param {{
 *   appOrigin: string,
 *   apiPath: string,
 *   github: ReturnType<typeof import("./github").createGitHubClient>,
 *   signInAttempts: ReturnType<typeof import("./signInAttempts").createSignInAttempts>,
 *   sessions: ReturnType<typeof import("./sessions").createSessions>,
 *   users: { saveSignedInUser: Function },
 *   signInLimiter: ReturnType<typeof import("./slidingWindowLimiter").createSlidingWindowLimiter>,
 *   now?: () => number,
 * }} dependencies
 */
function createGitHubSignInRouter({
  appOrigin,
  apiPath,
  github,
  signInAttempts,
  sessions,
  users,
  signInLimiter,
  now = Date.now,
}) {
  const router = express.Router();
  const redirectUri = callbackUrlFor(appOrigin, apiPath);

  router.get(LOGIN_PATH, async (request, response) => {
    response.set("Cache-Control", "no-store");
    if (!signInLimiter.take(request.ip ?? UNKNOWN_ADDRESS).taken) {
      logInfo("Sign-in attempts from one address reached the limit");
      response.redirect(siteAddress(SignInOutcome.RATE_LIMITED));
      return;
    }
    const attempt = await signInAttempts.start();
    signInAttempts.setCookie(response, attempt.token);
    response.redirect(
      github.authorizationUrl({
        state: attempt.state,
        codeChallenge: attempt.codeChallenge,
        redirectUri,
      }),
    );
  });

  router.get(CALLBACK_PATH, async (request, response) => {
    signInAttempts.clearCookie(response);
    response.set("Cache-Control", "no-store");
    const outcome = await completeSignIn(request, response);
    response.redirect(siteAddress(outcome));
  });

  /**
   * The site's front page, naming the sign-in outcome unless it succeeded.
   * @param {string | null} outcome
   */
  function siteAddress(outcome) {
    const destination = new URL("/", appOrigin);
    if (outcome !== null) {
      destination.searchParams.set(SIGN_IN_OUTCOME_PARAMETER, outcome);
    }
    return destination.toString();
  }

  /**
   * Finish the sign-in; return null on success, or the outcome to show the person.
   * @param {import("express").Request} request
   * @param {import("express").Response} response
   */
  async function completeSignIn(request, response) {
    const { code, state, error } = request.query;
    if (error !== undefined) {
      return refusedOnGitHub(error);
    }
    const attemptToken = readCookie(request, SIGN_IN_ATTEMPT_COOKIE_NAME);
    const attempt = attemptToken === null ? null : await signInAttempts.read(attemptToken);
    if (attempt === null) {
      logInfo("GitHub sign-in callback arrived without a current sign-in attempt");
      return SignInOutcome.EXPIRED;
    }
    if (!sameValue(attempt.state, state)) {
      logError("GitHub sign-in failed: state did not match the sign-in attempt");
      return SignInOutcome.FAILED;
    }
    if (typeof code !== "string" || code.length === 0 || code.length > MAX_CODE_LENGTH) {
      logError("GitHub sign-in failed: the callback had no usable code");
      return SignInOutcome.FAILED;
    }
    try {
      const accessToken = await github.exchangeCode({
        code,
        codeVerifier: attempt.codeVerifier,
        redirectUri,
      });
      const profile = await github.fetchProfile(accessToken);
      const user = await users.saveSignedInUser(profile, new Date(now()));
      sessions.setCookie(response, await sessions.issue(user));
      return null;
    } catch (caught) {
      logError(`GitHub sign-in failed: ${describeFailure(caught)}`);
      return SignInOutcome.FAILED;
    }
  }

  return router;
}

/** @param {unknown} error */
function refusedOnGitHub(error) {
  if (error === ACCESS_DENIED_ERROR) {
    logInfo("GitHub sign-in was declined on GitHub");
    return SignInOutcome.DENIED;
  }
  logError(`GitHub sign-in failed: GitHub returned ${safeErrorCode(error)}`);
  return SignInOutcome.FAILED;
}

/** @param {unknown} error */
function describeFailure(error) {
  if (error instanceof GitHubSignInError) {
    return error.message;
  }
  return redactError(error);
}

module.exports = {
  CALLBACK_PATH,
  LOGIN_PATH,
  SignInOutcome,
  callbackUrlFor,
  createGitHubSignInRouter,
};
