const { EventEmitter } = require("node:events");
const { stopOnSignals } = require("../src/shutdown");

function createFakes({ serverCloses = true, databaseFails = false } = {}) {
  const steps = [];
  const processLike = new EventEmitter();
  processLike.exit = jest.fn((code) => steps.push(`exit ${code}`));
  const server = {
    close: jest.fn((callback) => {
      steps.push("server closing");
      if (serverCloses) {
        setImmediate(() => {
          steps.push("server closed");
          callback();
        });
      }
    }),
    closeIdleConnections: jest.fn(() => steps.push("idle connections closed")),
  };
  const databaseClient = {
    close: jest.fn(async () => {
      steps.push("database closed");
      if (databaseFails) {
        throw new Error("could not reach mongodb+srv://user:secret-password@cluster.example");
      }
    }),
  };
  return { steps, processLike, server, databaseClient };
}

function waitForExit(processLike) {
  return new Promise((resolve) => {
    const exit = processLike.exit;
    processLike.exit = jest.fn((code) => {
      exit(code);
      resolve(code);
    });
  });
}

let consoleLog;
let consoleError;

beforeEach(() => {
  consoleLog = jest.spyOn(console, "log").mockImplementation(() => {});
  consoleError = jest.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  jest.useRealTimers();
  consoleLog.mockRestore();
  consoleError.mockRestore();
});

test.each(["SIGTERM", "SIGINT"])(
  "%s lets requests finish, then closes the database and exits with 0",
  async (signal) => {
    const { steps, processLike, server, databaseClient } = createFakes();
    stopOnSignals({ server, databaseClient, processLike });
    const exited = waitForExit(processLike);

    processLike.emit(signal, signal);

    expect(await exited).toBe(0);
    expect(steps).toEqual([
      "server closing",
      "idle connections closed",
      "server closed",
      "database closed",
      "exit 0",
    ]);
  },
);

test("a second signal while stopping is ignored", async () => {
  const { processLike, server, databaseClient } = createFakes();
  stopOnSignals({ server, databaseClient, processLike });
  const exited = waitForExit(processLike);

  processLike.emit("SIGTERM", "SIGTERM");
  processLike.emit("SIGINT", "SIGINT");
  await exited;

  expect(server.close).toHaveBeenCalledTimes(1);
  expect(processLike.exit).toHaveBeenCalledTimes(1);
});

test("requests still running after the grace period are cut off with exit code 1", () => {
  jest.useFakeTimers();
  const { processLike, server, databaseClient } = createFakes({ serverCloses: false });
  stopOnSignals({ server, databaseClient, processLike, graceMilliseconds: 1000 });

  processLike.emit("SIGTERM", "SIGTERM");
  jest.advanceTimersByTime(999);
  expect(processLike.exit).not.toHaveBeenCalled();
  jest.advanceTimersByTime(1);

  expect(processLike.exit).toHaveBeenCalledWith(1);
  expect(databaseClient.close).not.toHaveBeenCalled();
});

test("a failure while stopping exits with 1 and logs it without secrets", async () => {
  const { processLike, server, databaseClient } = createFakes({ databaseFails: true });
  stopOnSignals({ server, databaseClient, processLike });
  const exited = waitForExit(processLike);

  processLike.emit("SIGTERM", "SIGTERM");

  expect(await exited).toBe(1);
  const logged = consoleError.mock.calls.flat().join("\n");
  expect(logged).toContain("Could not stop cleanly");
  expect(logged).not.toContain("secret-password");
});
