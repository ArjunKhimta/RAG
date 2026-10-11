/**
 * Stop the API cleanly when the host asks it to.
 *
 * Docker and Render stop a container by sending SIGTERM, then kill it if it is still running
 * after a stop period (Docker's default is 10 seconds; `compose.yaml` sets 35). Inside a
 * container the API is the first process, and Linux gives the first process no default action
 * for SIGTERM, so without a handler the signal is ignored and every stop or deploy waits for the
 * kill, cutting off requests in progress.
 *
 * On the first SIGTERM or SIGINT: stop accepting connections, close idle keep-alive connections,
 * let requests in progress finish, close the database connection, and exit with 0. If that takes
 * longer than the 25-second grace period (a question can take up to 90 seconds), exit with 1
 * anyway rather than wait for the kill. Later signals are ignored.
 */

const { logError, logInfo } = require("./logging");
const { redactError } = require("./redaction");

const STOP_SIGNALS = ["SIGTERM", "SIGINT"];

const GRACE_MILLISECONDS = 25_000;

const STOPPED_EXIT_CODE = 0;

const FORCED_EXIT_CODE = 1;

/**
 * @param {{
 *   server: { close: (callback: (error?: Error) => void) => void, closeIdleConnections: () => void },
 *   databaseClient: { close: () => Promise<void> },
 *   processLike?: { once: (signal: string, listener: (signal: string) => void) => void, exit: (code: number) => void },
 *   graceMilliseconds?: number,
 * }} options
 */
function stopOnSignals({
  server,
  databaseClient,
  processLike = process,
  graceMilliseconds = GRACE_MILLISECONDS,
}) {
  let stopping = false;

  async function stop(signal) {
    if (stopping) {
      return;
    }
    stopping = true;
    logInfo(`Received ${signal}; stopping after requests in progress finish`);
    const forcedExit = setTimeout(() => {
      logError(`Requests still in progress after ${graceMilliseconds} ms; stopping anyway`);
      processLike.exit(FORCED_EXIT_CODE);
    }, graceMilliseconds);
    forcedExit.unref();
    try {
      await closeServer(server);
      await databaseClient.close();
    } catch (error) {
      clearTimeout(forcedExit);
      logError(`Could not stop cleanly: ${redactError(error)}`);
      processLike.exit(FORCED_EXIT_CODE);
      return;
    }
    clearTimeout(forcedExit);
    logInfo("Stopped");
    processLike.exit(STOPPED_EXIT_CODE);
  }

  for (const signal of STOP_SIGNALS) {
    processLike.once(signal, stop);
  }
}

function closeServer(server) {
  return new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
    server.closeIdleConnections();
  });
}

module.exports = { GRACE_MILLISECONDS, STOP_SIGNALS, stopOnSignals };
