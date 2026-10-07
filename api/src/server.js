/**
 * Start the API: load `api/.env` if present, read and check the settings, and listen.
 *
 * A setting that is missing or unsafe stops start-up with a message naming the variable, never
 * its value.
 */

const { createApp } = require("./app");
const {
  InvalidConfigError,
  MissingConfigError,
  loadEnvironmentFile,
  readConfig,
} = require("./config");
const { logError, logInfo } = require("./logging");
const { redactError } = require("./redaction");

const FAILED_EXIT_CODE = 1;

function main() {
  loadEnvironmentFile();
  const config = readConfig(process.env);
  const app = createApp();
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

try {
  main();
} catch (error) {
  stopWithError(error);
}
