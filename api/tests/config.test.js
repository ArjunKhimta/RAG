const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const {
  DEFAULT_HOST,
  DEFAULT_PORT,
  InvalidConfigError,
  MissingConfigError,
  loadEnvironmentFile,
  readConfig,
  readDatabaseSettings,
  readGitHubSettings,
  readRequired,
  readRequiredSecret,
  readRetrievalServiceSettings,
  readSessionSettings,
} = require("../src/config");
const { REDACTION_PLACEHOLDER, clearRegisteredSecrets, redact } = require("../src/redaction");

const STRONG_SECRET = "a".repeat(31) + "b";

afterEach(() => {
  clearRegisteredSecrets();
});

test("host and port default to a local-only address on port 3000", () => {
  expect(readConfig({})).toEqual({ host: DEFAULT_HOST, port: DEFAULT_PORT });
  expect(DEFAULT_HOST).toBe("127.0.0.1");
});

test("host and port come from the environment when set", () => {
  expect(readConfig({ HOST: "0.0.0.0", PORT: "8080" })).toEqual({ host: "0.0.0.0", port: 8080 });
});

test.each(["0", "65536", "80.5", "eighty", "-1"])("port %s is refused", (port) => {
  expect(() => readConfig({ PORT: port })).toThrow(InvalidConfigError);
});

test("a missing required value names the variable", () => {
  expect(() => readRequired({}, "MONGODB_URI")).toThrow(new MissingConfigError("MONGODB_URI"));
});

test("an empty required value counts as missing", () => {
  expect(() => readRequired({ MONGODB_URI: "" }, "MONGODB_URI")).toThrow(MissingConfigError);
});

test("a strong secret is returned and registered for redaction", () => {
  const secret = readRequiredSecret({ SESSION_SECRET: STRONG_SECRET }, "SESSION_SECRET");

  expect(secret).toBe(STRONG_SECRET);
  expect(redact(`value ${STRONG_SECRET}`)).toBe(`value ${REDACTION_PLACEHOLDER}`);
});

test("a secret under 32 characters is refused without showing it, and still redacted", () => {
  const weakSecret = "w".repeat(31);

  let caught;
  try {
    readRequiredSecret({ SESSION_SECRET: weakSecret }, "SESSION_SECRET");
  } catch (error) {
    caught = error;
  }

  expect(caught).toBeInstanceOf(InvalidConfigError);
  expect(caught.message).toBe("SESSION_SECRET must be at least 32 characters");
  expect(redact(weakSecret)).toBe(REDACTION_PLACEHOLDER);
});

test("a missing secret is refused", () => {
  expect(() => readRequiredSecret({}, "SESSION_SECRET")).toThrow(MissingConfigError);
});

describe("loading an environment file", () => {
  let folder;

  beforeEach(() => {
    folder = fs.mkdtempSync(path.join(os.tmpdir(), "api-config-"));
  });

  afterEach(() => {
    fs.rmSync(folder, { recursive: true, force: true });
  });

  test("values are loaded, and a variable already set keeps its value", () => {
    const filePath = path.join(folder, ".env");
    fs.writeFileSync(filePath, "FROM_FILE=file\nALREADY_SET=file\nEMPTY_BEFORE=file\n");
    const environment = { ALREADY_SET: "environment", EMPTY_BEFORE: "" };

    loadEnvironmentFile(filePath, environment);

    expect(environment).toEqual({
      FROM_FILE: "file",
      ALREADY_SET: "environment",
      EMPTY_BEFORE: "",
    });
  });

  test("a missing file leaves the environment unchanged", () => {
    const environment = { KEPT: "yes" };

    loadEnvironmentFile(path.join(folder, "missing.env"), environment);

    expect(environment).toEqual({ KEPT: "yes" });
  });
});

describe("session settings", () => {
  const SECRET = "s".repeat(48);
  const ORIGIN_ONLY = "must be only a scheme, host, and port, such as http://localhost:5173";

  test.each([
    ["http://localhost:5173", "http://localhost:5173"],
    ["http://localhost:5173/", "http://localhost:5173"],
    ["https://repositorycite.example", "https://repositorycite.example"],
  ])("origin %s is accepted as %s with a strong secret", (origin, expected) => {
    const settings = readSessionSettings({ SESSION_SECRET: SECRET, APP_ORIGIN: origin });

    expect(settings).toEqual({ sessionSecret: SECRET, appOrigin: expected });
    expect(redact(SECRET)).toBe(REDACTION_PLACEHOLDER);
  });

  test.each([
    ["http://repositorycite.example", "must use https unless it points at this machine"],
    ["localhost:5173", "must use https unless it points at this machine"],
    ["not a url", "must be a URL"],
    ["http://localhost:5173/app", ORIGIN_ONLY],
    ["http://localhost:5173/?next=/", ORIGIN_ONLY],
    ["https://user:pw@repositorycite.example", ORIGIN_ONLY],
  ])("origin %s is refused", (origin, requirement) => {
    expect(() => readSessionSettings({ SESSION_SECRET: SECRET, APP_ORIGIN: origin })).toThrow(
      new InvalidConfigError("APP_ORIGIN", requirement),
    );
  });

  test("a missing origin or a weak secret is refused", () => {
    expect(() => readSessionSettings({ SESSION_SECRET: SECRET })).toThrow(
      new MissingConfigError("APP_ORIGIN"),
    );
    expect(() =>
      readSessionSettings({ SESSION_SECRET: "short", APP_ORIGIN: "http://localhost:5173" }),
    ).toThrow(new InvalidConfigError("SESSION_SECRET", "must be at least 32 characters"));
  });
});

describe("GitHub settings", () => {
  const CLIENT_SECRET = "c".repeat(40);

  test("the ID and secret are returned and the secret is redacted", () => {
    const settings = readGitHubSettings({
      GITHUB_CLIENT_ID: "Ov23liExample",
      GITHUB_CLIENT_SECRET: CLIENT_SECRET,
    });

    expect(settings).toEqual({
      githubClientId: "Ov23liExample",
      githubClientSecret: CLIENT_SECRET,
    });
    expect(redact(CLIENT_SECRET)).toBe(REDACTION_PLACEHOLDER);
  });

  test("a missing ID or a short secret is refused", () => {
    expect(() => readGitHubSettings({ GITHUB_CLIENT_SECRET: CLIENT_SECRET })).toThrow(
      new MissingConfigError("GITHUB_CLIENT_ID"),
    );
    expect(() =>
      readGitHubSettings({ GITHUB_CLIENT_ID: "Ov23liExample", GITHUB_CLIENT_SECRET: "short" }),
    ).toThrow(new InvalidConfigError("GITHUB_CLIENT_SECRET", "must be at least 32 characters"));
  });
});

describe("database settings", () => {
  test("the URI is returned and redacted from any text", () => {
    const uri = "mongodb+srv://user:password@cluster0.example.mongodb.net/?appName=x";

    expect(readDatabaseSettings({ MONGODB_URI: uri })).toEqual({ mongodbUri: uri });
    expect(redact(`failed to reach ${uri}`)).toBe(`failed to reach ${REDACTION_PLACEHOLDER}`);
  });

  test("a missing URI is refused", () => {
    expect(() => readDatabaseSettings({})).toThrow(new MissingConfigError("MONGODB_URI"));
  });
});

describe("retrieval service settings", () => {
  const TOKEN = "t".repeat(40);
  const NO_EXTRAS = "must not include a user name, password, query, or fragment";

  test.each(["https://retrieval.example.com", "http://127.0.0.1:5001", "http://localhost:5001/"])(
    "%s is accepted with a strong token",
    (url) => {
      const settings = readRetrievalServiceSettings({
        RETRIEVAL_SERVICE_URL: url,
        RETRIEVAL_SERVICE_TOKEN: TOKEN,
      });

      expect(settings).toEqual({ baseUrl: url, token: TOKEN });
      expect(redact(TOKEN)).toBe(REDACTION_PLACEHOLDER);
    },
  );

  test.each([
    ["http://retrieval.example.com", "must use https unless it points at this machine"],
    ["ftp://127.0.0.1", "must use https unless it points at this machine"],
    ["not a url", "must be a URL"],
    ["https://user:pw@retrieval.example.com", NO_EXTRAS],
    ["https://retrieval.example.com/?token=x", NO_EXTRAS],
  ])("%s is refused without showing the value", (url, requirement) => {
    const environment = { RETRIEVAL_SERVICE_URL: url, RETRIEVAL_SERVICE_TOKEN: TOKEN };

    expect(() => readRetrievalServiceSettings(environment)).toThrow(
      new InvalidConfigError("RETRIEVAL_SERVICE_URL", requirement),
    );
  });

  test("a missing URL or a weak token is refused", () => {
    expect(() => readRetrievalServiceSettings({ RETRIEVAL_SERVICE_TOKEN: TOKEN })).toThrow(
      MissingConfigError,
    );
    expect(() =>
      readRetrievalServiceSettings({
        RETRIEVAL_SERVICE_URL: "http://127.0.0.1:5001",
        RETRIEVAL_SERVICE_TOKEN: "short-token",
      }),
    ).toThrow(new InvalidConfigError("RETRIEVAL_SERVICE_TOKEN", "must be at least 32 characters"));
  });
});
