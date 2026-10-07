/**
 * The two calls to GitHub that sign someone in, and the link that starts it.
 *
 * 1. The browser is sent to GitHub's authorize page with our client ID, the callback address, a
 *    random `state`, and a PKCE `code_challenge` (the SHA-256 of a secret `code_verifier`). No
 *    `scope` is sent, so GitHub grants no access to anything; it only confirms who the person is.
 * 2. GitHub sends the browser back with a one-time `code`, which the API trades for an access token,
 *    proving with the client secret that it is our app and with the `code_verifier` that it is
 *    the same party that started the sign-in.
 * 3. The API asks `GET /user` who the token belongs to, once. The token is then dropped: it is
 *    never stored, logged, or sent anywhere else, and GitHub expires it after 8 hours.
 *
 * Each call has a 10-second time limit and never follows a redirect. A failure becomes a
 * `GitHubSignInError` whose description names the step and status (and GitHub's short error code,
 * such as `bad_verification_code`), never a reply body, so the log can say what went wrong
 * without holding anything sensitive.
 */

const GITHUB_OAUTH_URL = "https://github.com";

const GITHUB_API_URL = "https://api.github.com";

const AUTHORIZE_PATH = "/login/oauth/authorize";

const ACCESS_TOKEN_PATH = "/login/oauth/access_token";

const USER_PATH = "/user";

const GITHUB_API_VERSION = "2026-03-10";

const USER_AGENT = "RepositoryCite";

const TIMEOUT_MILLISECONDS = 10_000;

const CODE_CHALLENGE_METHOD = "S256";

const ERROR_CODE_PATTERN = /^[a-z_]{1,64}$/;

const LOGIN_PATTERN = /^[A-Za-z0-9-]{1,39}$/;

class GitHubSignInError extends Error {
  /** @param {string} description safe to log: a step and a status, never a reply body */
  constructor(description) {
    super(description);
    this.name = "GitHubSignInError";
  }
}

/**
 * @param {{
 *   clientId: string,
 *   clientSecret: string,
 *   oauthUrl?: string,
 *   apiUrl?: string,
 *   fetchFunction?: typeof fetch,
 *   timeoutMilliseconds?: number,
 * }} options
 */
function createGitHubClient({
  clientId,
  clientSecret,
  oauthUrl = GITHUB_OAUTH_URL,
  apiUrl = GITHUB_API_URL,
  fetchFunction = globalThis.fetch,
  timeoutMilliseconds = TIMEOUT_MILLISECONDS,
}) {
  async function call(step, url, options) {
    let response;
    let body;
    try {
      response = await fetchFunction(url, {
        ...options,
        signal: AbortSignal.timeout(timeoutMilliseconds),
        redirect: "error",
      });
      body = await readJson(response);
    } catch (error) {
      throw new GitHubSignInError(`${step} could not reach GitHub (${error.name})`);
    }
    if (response.status !== 200) {
      throw new GitHubSignInError(`${step} failed with status ${response.status}`);
    }
    if (!isObject(body)) {
      throw new GitHubSignInError(`${step} reply was not a JSON object`);
    }
    return body;
  }

  return {
    /**
     * The GitHub page that asks the person to approve the sign-in.
     * @param {{ state: string, codeChallenge: string, redirectUri: string }} attempt
     * @returns {string}
     */
    authorizationUrl({ state, codeChallenge, redirectUri }) {
      const url = new URL(AUTHORIZE_PATH, oauthUrl);
      url.searchParams.set("client_id", clientId);
      url.searchParams.set("redirect_uri", redirectUri);
      url.searchParams.set("state", state);
      url.searchParams.set("code_challenge", codeChallenge);
      url.searchParams.set("code_challenge_method", CODE_CHALLENGE_METHOD);
      return url.toString();
    },

    /**
     * Trade the one-time code for an access token.
     * @param {{ code: string, codeVerifier: string, redirectUri: string }} exchange
     * @returns {Promise<string>}
     */
    async exchangeCode({ code, codeVerifier, redirectUri }) {
      const body = await call("token exchange", new URL(ACCESS_TOKEN_PATH, oauthUrl), {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/x-www-form-urlencoded",
          "User-Agent": USER_AGENT,
        },
        body: new URLSearchParams({
          client_id: clientId,
          client_secret: clientSecret,
          code,
          redirect_uri: redirectUri,
          code_verifier: codeVerifier,
        }).toString(),
      });
      if (body.error !== undefined) {
        throw new GitHubSignInError(`token exchange refused (${safeErrorCode(body.error)})`);
      }
      if (typeof body.access_token !== "string" || body.access_token.length === 0) {
        throw new GitHubSignInError("token exchange reply had no access token");
      }
      return body.access_token;
    },

    /**
     * Ask GitHub who the token belongs to.
     * @param {string} accessToken
     * @returns {Promise<{ githubId: number, login: string, name: string | null, avatarUrl: string }>}
     */
    async fetchProfile(accessToken) {
      const body = await call("profile lookup", new URL(USER_PATH, apiUrl), {
        method: "GET",
        headers: {
          Authorization: `Bearer ${accessToken}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": GITHUB_API_VERSION,
          "User-Agent": USER_AGENT,
        },
      });
      return readProfile(body);
    },
  };
}

/** @param {Record<string, unknown>} body */
function readProfile(body) {
  const { id, login, name, avatar_url: avatarUrl } = body;
  if (!Number.isSafeInteger(id) || id < 1) {
    throw new GitHubSignInError("profile had no valid id");
  }
  if (typeof login !== "string" || !LOGIN_PATTERN.test(login)) {
    throw new GitHubSignInError("profile had no valid login");
  }
  if (name !== null && name !== undefined && typeof name !== "string") {
    throw new GitHubSignInError("profile name was not text");
  }
  if (!isHttpsUrl(avatarUrl)) {
    throw new GitHubSignInError("profile had no https avatar URL");
  }
  return { githubId: id, login, name: name || null, avatarUrl };
}

/** @param {unknown} value */
function safeErrorCode(value) {
  return typeof value === "string" && ERROR_CODE_PATTERN.test(value) ? value : "unknown error";
}

/** @param {unknown} value */
function isHttpsUrl(value) {
  if (typeof value !== "string") {
    return false;
  }
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

/** @param {Response} response */
async function readJson(response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

/** @param {unknown} value */
function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

module.exports = {
  CODE_CHALLENGE_METHOD,
  GITHUB_API_VERSION,
  GitHubSignInError,
  USER_AGENT,
  createGitHubClient,
  safeErrorCode,
};
