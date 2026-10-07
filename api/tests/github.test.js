const http = require("node:http");

const {
  GITHUB_API_VERSION,
  GitHubSignInError,
  USER_AGENT,
  createGitHubClient,
} = require("../src/github");

const CLIENT_ID = "Ov23liTestClientId01";

const CLIENT_SECRET = "test-client-secret-0123456789abcdef01234567";

const ACCESS_TOKEN = "gho_test-access-token-0123456789";

const PROFILE_REPLY = {
  id: 583231,
  login: "octocat",
  name: "The Octocat",
  avatar_url: "https://avatars.githubusercontent.com/u/583231?v=4",
  email: "octocat@github.com",
  site_admin: false,
};

let gitHub;

beforeEach(async () => {
  gitHub = await startFakeGitHub();
});

afterEach(async () => {
  await gitHub.close();
});

function client(options = {}) {
  return createGitHubClient({
    clientId: CLIENT_ID,
    clientSecret: CLIENT_SECRET,
    oauthUrl: gitHub.url,
    apiUrl: gitHub.url,
    ...options,
  });
}

async function caught(promise) {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("expected the call to fail");
}

const EXCHANGE = {
  code: "one-time-code",
  codeVerifier: "v".repeat(43),
  redirectUri: "http://localhost:5173/api/auth/github/callback",
};

describe("the authorize link", () => {
  test("carries the client ID, callback, state, and S256 challenge, and asks for no scope", () => {
    const link = new URL(
      createGitHubClient({ clientId: CLIENT_ID, clientSecret: CLIENT_SECRET }).authorizationUrl({
        state: "the-state",
        codeChallenge: "the-challenge",
        redirectUri: EXCHANGE.redirectUri,
      }),
    );

    expect(`${link.origin}${link.pathname}`).toBe("https://github.com/login/oauth/authorize");
    expect(Object.fromEntries(link.searchParams)).toEqual({
      client_id: CLIENT_ID,
      redirect_uri: EXCHANGE.redirectUri,
      state: "the-state",
      code_challenge: "the-challenge",
      code_challenge_method: "S256",
    });
  });
});

describe("trading the code for a token", () => {
  test("posts the code, verifier, callback, and app credentials as a form, and returns the token", async () => {
    gitHub.reply("/login/oauth/access_token", {
      status: 200,
      body: { access_token: ACCESS_TOKEN, token_type: "bearer", scope: "", expires_in: 28800 },
    });

    const token = await client().exchangeCode(EXCHANGE);

    expect(token).toBe(ACCESS_TOKEN);
    const sent = gitHub.requests[0];
    expect(sent.method).toBe("POST");
    expect(sent.headers.accept).toBe("application/json");
    expect(sent.headers["content-type"]).toBe("application/x-www-form-urlencoded");
    expect(sent.headers["user-agent"]).toBe(USER_AGENT);
    expect(Object.fromEntries(new URLSearchParams(sent.body))).toEqual({
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      code: EXCHANGE.code,
      redirect_uri: EXCHANGE.redirectUri,
      code_verifier: EXCHANGE.codeVerifier,
    });
  });

  test("GitHub's refusal, sent as a 200 with an error, names only GitHub's short code", async () => {
    gitHub.reply("/login/oauth/access_token", {
      status: 200,
      body: {
        error: "bad_verification_code",
        error_description: "The code passed is incorrect or expired.",
      },
    });

    const error = await caught(client().exchangeCode(EXCHANGE));

    expect(error).toBeInstanceOf(GitHubSignInError);
    expect(error.message).toBe("token exchange refused (bad_verification_code)");
  });

  test.each([
    ["an error code that is not a plain code", { status: 200, body: { error: "<b>no</b>" } }, "token exchange refused (unknown error)"],
    ["a reply without a token", { status: 200, body: { token_type: "bearer" } }, "token exchange reply had no access token"],
    ["a server error", { status: 500, body: { message: "oops" } }, "token exchange failed with status 500"],
    ["a reply that is not JSON", { status: 200, rawBody: "access_token=x" }, "token exchange reply was not a JSON object"],
  ])("%s is a sign-in error", async (description, reply, message) => {
    gitHub.reply("/login/oauth/access_token", reply);

    const error = await caught(client().exchangeCode(EXCHANGE));

    expect(error).toBeInstanceOf(GitHubSignInError);
    expect(error.message).toBe(message);
    expect(error.message).not.toContain(CLIENT_SECRET);
  });
});

describe("asking who the token belongs to", () => {
  test("sends the token with GitHub's API headers and keeps only the profile fields", async () => {
    gitHub.reply("/user", { status: 200, body: PROFILE_REPLY });

    const profile = await client().fetchProfile(ACCESS_TOKEN);

    expect(profile).toEqual({
      githubId: 583231,
      login: "octocat",
      name: "The Octocat",
      avatarUrl: "https://avatars.githubusercontent.com/u/583231?v=4",
    });
    const sent = gitHub.requests[0];
    expect(sent.method).toBe("GET");
    expect(sent.headers.authorization).toBe(`Bearer ${ACCESS_TOKEN}`);
    expect(sent.headers.accept).toBe("application/vnd.github+json");
    expect(sent.headers["x-github-api-version"]).toBe(GITHUB_API_VERSION);
    expect(sent.headers["user-agent"]).toBe(USER_AGENT);
  });

  test.each([null, ""])("a name of %p is stored as no name", async (name) => {
    gitHub.reply("/user", { status: 200, body: { ...PROFILE_REPLY, name } });

    expect((await client().fetchProfile(ACCESS_TOKEN)).name).toBeNull();
  });

  test.each([
    ["an ID that is text", { id: "583231" }, "profile had no valid id"],
    ["an ID of 0", { id: 0 }, "profile had no valid id"],
    ["a login with a slash", { login: "octo/cat" }, "profile had no valid login"],
    ["a login over 39 characters", { login: "o".repeat(40) }, "profile had no valid login"],
    ["a name that is a number", { name: 7 }, "profile name was not text"],
    ["an http avatar", { avatar_url: "http://example.com/a.png" }, "profile had no https avatar URL"],
    ["a script avatar", { avatar_url: "javascript:alert(1)" }, "profile had no https avatar URL"],
  ])("a profile with %s is refused", async (description, change, message) => {
    gitHub.reply("/user", { status: 200, body: { ...PROFILE_REPLY, ...change } });

    const error = await caught(client().fetchProfile(ACCESS_TOKEN));

    expect(error).toBeInstanceOf(GitHubSignInError);
    expect(error.message).toBe(message);
  });

  test("a refused token is a sign-in error naming the status, never the token", async () => {
    gitHub.reply("/user", { status: 401, body: { message: "Bad credentials" } });

    const error = await caught(client().fetchProfile(ACCESS_TOKEN));

    expect(error.message).toBe("profile lookup failed with status 401");
    expect(error.message).not.toContain(ACCESS_TOKEN);
  });
});

describe("GitHub out of reach", () => {
  test("a reply slower than the time limit is a sign-in error", async () => {
    gitHub.reply("/user", { status: 200, body: PROFILE_REPLY, delayMilliseconds: 500 });

    const error = await caught(client({ timeoutMilliseconds: 50 }).fetchProfile(ACCESS_TOKEN));

    expect(error).toBeInstanceOf(GitHubSignInError);
    expect(error.message).toBe("profile lookup could not reach GitHub (TimeoutError)");
  });

  test("a redirect is never followed, so the token goes nowhere else", async () => {
    const elsewhere = await startFakeGitHub();
    gitHub.reply("/user", {
      status: 302,
      headers: { Location: `${elsewhere.url}/user` },
      rawBody: "",
    });

    const error = await caught(client().fetchProfile(ACCESS_TOKEN));

    expect(error).toBeInstanceOf(GitHubSignInError);
    expect(elsewhere.requests).toHaveLength(0);
    await elsewhere.close();
  });
});

function startFakeGitHub() {
  const requests = [];
  const replies = new Map();

  const server = http.createServer((incoming, outgoing) => {
    const chunks = [];
    incoming.on("data", (chunk) => chunks.push(chunk));
    incoming.on("end", () => {
      const path = new URL(incoming.url, "http://fake").pathname;
      requests.push({
        method: incoming.method,
        path,
        headers: incoming.headers,
        body: Buffer.concat(chunks).toString("utf8"),
      });
      const reply = replies.get(path) ?? { status: 404, rawBody: "no reply set" };
      const send = () => {
        const isJson = reply.body !== undefined;
        outgoing.writeHead(reply.status, {
          ...(isJson ? { "Content-Type": "application/json" } : {}),
          ...(reply.headers || {}),
        });
        outgoing.end(isJson ? JSON.stringify(reply.body) : reply.rawBody);
      };
      if (reply.delayMilliseconds) {
        setTimeout(send, reply.delayMilliseconds);
      } else {
        send();
      }
    });
  });

  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({
        url: `http://127.0.0.1:${port}`,
        requests,
        reply(path, reply) {
          replies.set(path, reply);
        },
        close() {
          server.closeAllConnections();
          return new Promise((done) => server.close(done));
        },
      });
    });
  });
}
