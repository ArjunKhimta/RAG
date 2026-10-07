const crypto = require("node:crypto");

const { loadEnvironmentFile, readDatabaseSettings } = require("../src/config");
const { connectToDatabase } = require("../src/database");
const {
  USAGE_COLLECTION,
  USAGE_LIFETIME_SECONDS,
  UsageKind,
  createMongoUsageStore,
} = require("../src/usage");
const {
  FIRST_USER,
  NOW,
  TODAY,
  describeUsageStoreContract,
} = require("./support/usageStoreContract");

const TEST_DATABASE_PREFIX = "code_search_app_test_";

const ATLAS_TIMEOUT_MILLISECONDS = 30_000;

jest.setTimeout(ATLAS_TIMEOUT_MILLISECONDS);

let client;
let database;
let store;

beforeAll(async () => {
  const environment = { ...process.env };
  loadEnvironmentFile(undefined, environment);
  const { mongodbUri } = readDatabaseSettings(environment);
  const databaseName = `${TEST_DATABASE_PREFIX}${crypto.randomBytes(4).toString("hex")}`;
  ({ client, database } = await connectToDatabase(mongodbUri, databaseName));
  store = createMongoUsageStore(database);
  await store.ensureIndexes();
});

afterAll(async () => {
  if (database !== undefined) {
    await database.dropDatabase();
  }
  if (client !== undefined) {
    await client.close();
  }
});

async function emptyStore() {
  await database.collection(USAGE_COLLECTION).deleteMany({});
  return store;
}

describe("the MongoDB usage store against real Atlas", () => {
  describeUsageStoreContract(emptyStore);

  test("40 bookings at once against a limit of 30 give exactly 30, on a day not yet created", async () => {
    await emptyStore();

    const results = await Promise.all(
      Array.from({ length: 40 }, () =>
        store.book({
          userId: FIRST_USER,
          kind: UsageKind.QUESTIONS,
          limit: 30,
          day: TODAY,
          now: NOW,
        }),
      ),
    );

    expect(results.filter(Boolean)).toHaveLength(30);
    expect((await store.read(FIRST_USER, TODAY)).questions).toBe(30);
    expect(await database.collection(USAGE_COLLECTION).countDocuments({})).toBe(1);
  });

  test("usage documents expire 30 days after they are created", async () => {
    const indexes = await database.collection(USAGE_COLLECTION).indexes();
    const expiring = indexes.filter((index) => index.expireAfterSeconds !== undefined);

    expect(expiring.map((index) => [index.key, index.expireAfterSeconds])).toEqual([
      [{ created_at: 1 }, USAGE_LIFETIME_SECONDS],
    ]);
    expect(USAGE_LIFETIME_SECONDS).toBe(2_592_000);
  });
});
