/**
 * Settings from the environment, checked once at start-up so a missing or unsafe value stops the
 * API at launch rather than on the first request that needs it.
 *
 * Local runs read `api/.env` with Node's built-in parser (no dotenv package); a variable already
 * set in the environment wins over the file, as on the Python side. Secrets are registered with the
 * redaction module the moment they are read, and no error message ever includes a value.
 *
 * The API listens on 127.0.0.1 unless `HOST` says otherwise, so a local run is not reachable from
 * other machines on the network; the deployment sets `HOST` explicitly.
 *
 * `TRUST_PROXY_HOPS` says how many proxies stand between visitors and the API (0 locally; Vercel
 * and Render in production). Express then takes the visitor's address from that many entries of
 * `X-Forwarded-For`, counted from the right, where the proxies add them. At 0 the header is
 * ignored, so nobody can claim another address; too high a number would let a visitor put any
 * address at the left of the header and have it believed. `FORWARDING_CHECK=on` turns on a route
 * that shows callers their own forwarding chain, used once to count the hops on a new host.
 */

const fs = require("node:fs");
const path = require("node:path");
const util = require("node:util");

const { registerSecret } = require("./redaction");

const ENV_FILE = path.join(__dirname, "..", ".env");

const DEFAULT_HOST = "127.0.0.1";

const DEFAULT_PORT = 3000;

const HIGHEST_PORT = 65535;

const MINIMUM_SECRET_LENGTH = 32;

const RETRIEVAL_SERVICE_URL_VARIABLE = "RETRIEVAL_SERVICE_URL";

const RETRIEVAL_SERVICE_TOKEN_VARIABLE = "RETRIEVAL_SERVICE_TOKEN";

const SESSION_SECRET_VARIABLE = "SESSION_SECRET";

const APP_ORIGIN_VARIABLE = "APP_ORIGIN";

const MONGODB_URI_VARIABLE = "MONGODB_URI";

const GITHUB_CLIENT_ID_VARIABLE = "GITHUB_CLIENT_ID";

const GITHUB_CLIENT_SECRET_VARIABLE = "GITHUB_CLIENT_SECRET";

const LOCAL_HOSTNAMES = new Set(["localhost", "127.0.0.1", "[::1]"]);

const TRUST_PROXY_HOPS_VARIABLE = "TRUST_PROXY_HOPS";

const MAX_TRUST_PROXY_HOPS = 3;

const FORWARDING_CHECK_VARIABLE = "FORWARDING_CHECK";

const FORWARDING_CHECK_ON = "on";

class MissingConfigError extends Error {
  /** @param {string} variableName */
  constructor(variableName) {
    super(`${variableName} is not set`);
    this.name = "MissingConfigError";
    this.variableName = variableName;
  }
}

class InvalidConfigError extends Error {
  /**
   * @param {string} variableName
   * @param {string} requirement
   */
  constructor(variableName, requirement) {
    super(`${variableName} ${requirement}`);
    this.name = "InvalidConfigError";
    this.variableName = variableName;
  }
}

/**
 * Copy the variables in `filePath`, if it exists, into `environment`, keeping any already set.
 * @param {string} [filePath]
 * @param {NodeJS.ProcessEnv} [environment]
 */
function loadEnvironmentFile(filePath = ENV_FILE, environment = process.env) {
  const fileValues = util.parseEnv(readFileIfPresent(filePath));
  for (const [variableName, value] of Object.entries(fileValues)) {
    if (environment[variableName] === undefined) {
      environment[variableName] = value;
    }
  }
}

/** @param {string} filePath */
function readFileIfPresent(filePath) {
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") {
      return "";
    }
    throw error;
  }
}

/**
 * Read the settings the API needs to start.
 * @param {NodeJS.ProcessEnv} environment
 * @returns {{ host: string, port: number }}
 */
function readConfig(environment) {
  return {
    host: readHost(environment),
    port: readPort(environment),
  };
}

/**
 * Read where the retrieval service is and the token it expects. The URL must use https, except
 * for this machine (`localhost`, `127.0.0.1`, `[::1]`), so the token never crosses a network
 * unencrypted; it may not carry a user name, password, query, or fragment.
 * @param {NodeJS.ProcessEnv} environment
 * @returns {{ baseUrl: string, token: string }}
 */
function readRetrievalServiceSettings(environment) {
  const baseUrl = readRequired(environment, RETRIEVAL_SERVICE_URL_VARIABLE);
  requireSafeServiceUrl(baseUrl);
  const token = readRequiredSecret(environment, RETRIEVAL_SERVICE_TOKEN_VARIABLE);
  return { baseUrl, token };
}

/** @param {string} text */
function requireSafeServiceUrl(text) {
  const url = readSafeUrl(RETRIEVAL_SERVICE_URL_VARIABLE, text);
  if (url.username || url.password || url.search || url.hash) {
    throw new InvalidConfigError(
      RETRIEVAL_SERVICE_URL_VARIABLE,
      "must not include a user name, password, query, or fragment",
    );
  }
}

/**
 * Read what signs sessions and where the site lives. `APP_ORIGIN` is the address people open in
 * the browser (scheme, host, and port only); requests that change something must come from it, and
 * session cookies are marked `Secure` when it uses https.
 * @param {NodeJS.ProcessEnv} environment
 * @returns {{ sessionSecret: string, appOrigin: string }}
 */
function readSessionSettings(environment) {
  const sessionSecret = readRequiredSecret(environment, SESSION_SECRET_VARIABLE);
  const appOrigin = readAppOrigin(environment);
  return { sessionSecret, appOrigin };
}

/** @param {NodeJS.ProcessEnv} environment */
function readAppOrigin(environment) {
  const text = readRequired(environment, APP_ORIGIN_VARIABLE);
  const url = readSafeUrl(APP_ORIGIN_VARIABLE, text);
  if (url.origin !== text.replace(/\/$/, "")) {
    throw new InvalidConfigError(
      APP_ORIGIN_VARIABLE,
      "must be only a scheme, host, and port, such as http://localhost:5173",
    );
  }
  return url.origin;
}

/**
 * Read the GitHub OAuth App's ID and secret. The ID is public (it appears in the sign-in link);
 * the secret is checked like every other secret and registered for redaction.
 * @param {NodeJS.ProcessEnv} environment
 * @returns {{ githubClientId: string, githubClientSecret: string }}
 */
function readGitHubSettings(environment) {
  const githubClientId = readRequired(environment, GITHUB_CLIENT_ID_VARIABLE);
  const githubClientSecret = readRequiredSecret(environment, GITHUB_CLIENT_SECRET_VARIABLE);
  return { githubClientId, githubClientSecret };
}

/**
 * Read the database address; it holds a password, so it is registered for redaction.
 * @param {NodeJS.ProcessEnv} environment
 * @returns {{ mongodbUri: string }}
 */
function readDatabaseSettings(environment) {
  const mongodbUri = readRequired(environment, MONGODB_URI_VARIABLE);
  registerSecret(mongodbUri);
  return { mongodbUri };
}

/**
 * Parse `text` as a URL that uses https, or plain http only when it points at this machine.
 * @param {string} variableName
 * @param {string} text
 * @returns {URL}
 */
function readSafeUrl(variableName, text) {
  let url;
  try {
    url = new URL(text);
  } catch {
    throw new InvalidConfigError(variableName, "must be a URL");
  }
  const isLocal = LOCAL_HOSTNAMES.has(url.hostname);
  if (url.protocol !== "https:" && !(url.protocol === "http:" && isLocal)) {
    throw new InvalidConfigError(variableName, "must use https unless it points at this machine");
  }
  return url;
}

/**
 * Return a required secret, refusing one that is missing or shorter than 32 characters, and
 * register it for redaction.
 * @param {NodeJS.ProcessEnv} environment
 * @param {string} variableName
 * @returns {string}
 */
function readRequiredSecret(environment, variableName) {
  const value = readRequired(environment, variableName);
  registerSecret(value);
  if (value.length < MINIMUM_SECRET_LENGTH) {
    throw new InvalidConfigError(
      variableName,
      `must be at least ${MINIMUM_SECRET_LENGTH} characters`,
    );
  }
  return value;
}

/**
 * @param {NodeJS.ProcessEnv} environment
 * @param {string} variableName
 * @returns {string}
 */
function readRequired(environment, variableName) {
  const value = environment[variableName];
  if (!value) {
    throw new MissingConfigError(variableName);
  }
  return value;
}

/**
 * Read how many proxies to trust for the visitor's address, and whether the forwarding check
 * route is on.
 * @param {NodeJS.ProcessEnv} environment
 * @returns {{ trustProxyHops: number, forwardingCheck: boolean }}
 */
function readProxySettings(environment) {
  return {
    trustProxyHops: readTrustProxyHops(environment),
    forwardingCheck: readForwardingCheck(environment),
  };
}

/** @param {NodeJS.ProcessEnv} environment */
function readTrustProxyHops(environment) {
  const text = environment[TRUST_PROXY_HOPS_VARIABLE];
  if (!text) {
    return 0;
  }
  const hops = Number(text);
  if (!/^\d+$/.test(text) || hops > MAX_TRUST_PROXY_HOPS) {
    throw new InvalidConfigError(
      TRUST_PROXY_HOPS_VARIABLE,
      `must be a whole number from 0 to ${MAX_TRUST_PROXY_HOPS}`,
    );
  }
  return hops;
}

/** @param {NodeJS.ProcessEnv} environment */
function readForwardingCheck(environment) {
  const text = environment[FORWARDING_CHECK_VARIABLE];
  if (!text) {
    return false;
  }
  if (text !== FORWARDING_CHECK_ON) {
    throw new InvalidConfigError(FORWARDING_CHECK_VARIABLE, `must be "${FORWARDING_CHECK_ON}" or unset`);
  }
  return true;
}

/** @param {NodeJS.ProcessEnv} environment */
function readHost(environment) {
  return environment.HOST || DEFAULT_HOST;
}

/** @param {NodeJS.ProcessEnv} environment */
function readPort(environment) {
  const text = environment.PORT;
  if (!text) {
    return DEFAULT_PORT;
  }
  const port = Number(text);
  if (!Number.isInteger(port) || port < 1 || port > HIGHEST_PORT) {
    throw new InvalidConfigError("PORT", `must be a whole number from 1 to ${HIGHEST_PORT}`);
  }
  return port;
}

module.exports = {
  ENV_FILE,
  DEFAULT_HOST,
  DEFAULT_PORT,
  MINIMUM_SECRET_LENGTH,
  MissingConfigError,
  InvalidConfigError,
  loadEnvironmentFile,
  readConfig,
  readDatabaseSettings,
  readGitHubSettings,
  readProxySettings,
  readRequired,
  readRequiredSecret,
  readRetrievalServiceSettings,
  readSessionSettings,
};
