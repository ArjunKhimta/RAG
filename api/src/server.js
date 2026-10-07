/**
 * Start the API: load `api/.env` if present, read and check every setting, connect to the
 * database and make sure its indexes exist, and listen.
 *
 * A setting that is missing or unsafe stops start-up with a message naming the variable, never
 * its value; settings are all checked before connecting, so a typo is reported without waiting on
 * the network. A database that cannot be reached stops start-up with a redacted message.
 */

const { createApp } = require("./app");
const {
  InvalidConfigError,
  MissingConfigError,
  loadEnvironmentFile,
  readConfig,
  readDatabaseSettings,
  readGitHubSettings,
  readSessionSettings,
} = require("./config");
const { connectToDatabase } = require("./database");
const { createGitHubClient } = require("./github");
const { logError, logInfo } = require("./logging");
const { redactError } = require("./redaction");
const { createSessions } = require("./sessions");
const { createSignInAttempts } = require("./signInAttempts");
const { createMongoUserStore } = require("./users");

const FAILED_EXIT_CODE = 1;

const HTTPS_PROTOCOL_PREFIX = "https:";

async function main() {
  loadEnvironmentFile();
  const config = readConfig(process.env);
  const sessionSettings = readSessionSettings(process.env);
  const gitHubSettings = readGitHubSettings(process.env);
  const databaseSettings = readDatabaseSettings(process.env);

  const { database } = await connectToDatabase(databaseSettings.mongodbUri);
  const users = createMongoUserStore(database);
  await users.ensureIndexes();
  const cookieSettings = {
    sessionSecret: sessionSettings.sessionSecret,
    secureCookies: sessionSettings.appOrigin.startsWith(HTTPS_PROTOCOL_PREFIX),
  };
  const github = createGitHubClient({
    clientId: gitHubSettings.githubClientId,
    clientSecret: gitHubSettings.githubClientSecret,
  });

  const app = createApp({
    appOrigin: sessionSettings.appOrigin,
    github,
    signInAttempts: createSignInAttempts(cookieSettings),
    sessions: createSessions(cookieSettings),
    users,
  });
  const server = app.listen(config.port, config.host, () => {
    logInfo(`Listening on http://${config.host}:${config.port}`);
  });
  server.on("error", stopWithError);
}

/** @param {unknown} error */
function stopWithError(error) {
  logError(`Could not start the API: ${describeStartupError(error)}`);
  process.exit(FAILED_EXIT_CODE);
}

/** @param {unknown} error */
function describeStartupError(error) {
  if (error instanceof MissingConfigError || error instanceof InvalidConfigError) {
    return error.message;
  }
  return redactError(error);
}

main().catch(stopWithError);
