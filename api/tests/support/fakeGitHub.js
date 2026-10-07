const { GitHubSignInError } = require("../../src/github");
const { OCTOCAT } = require("./userStoreContract");

const FAKE_AUTHORIZE_URL = "https://github.example/login/oauth/authorize";

const FAKE_ACCESS_TOKEN = "gho_fake-access-token-for-tests";

/**
 * A stand-in for `createGitHubClient` that records what the routes asked for. Set `exchangeError`
 * or `profileError` to make a step fail, and `profile` to choose who signs in.
 */
function createFakeGitHub() {
  return {
    profile: OCTOCAT,
    exchangeError: null,
    profileError: null,
    authorizations: [],
    exchanges: [],
    profileTokens: [],

    authorizationUrl(attempt) {
      this.authorizations.push(attempt);
      const url = new URL(FAKE_AUTHORIZE_URL);
      url.searchParams.set("redirect_uri", attempt.redirectUri);
      url.searchParams.set("state", attempt.state);
      url.searchParams.set("code_challenge", attempt.codeChallenge);
      return url.toString();
    },

    async exchangeCode(exchange) {
      this.exchanges.push(exchange);
      if (this.exchangeError !== null) {
        throw this.exchangeError;
      }
      return FAKE_ACCESS_TOKEN;
    },

    async fetchProfile(accessToken) {
      this.profileTokens.push(accessToken);
      if (this.profileError !== null) {
        throw this.profileError;
      }
      return this.profile;
    },
  };
}

module.exports = {
  FAKE_ACCESS_TOKEN,
  FAKE_AUTHORIZE_URL,
  GitHubSignInError,
  createFakeGitHub,
};
