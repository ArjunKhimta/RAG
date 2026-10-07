const crypto = require("node:crypto");

const { loadEnvironmentFile, readDatabaseSettings } = require("../src/config");
const {
  CONVERSATIONS_COLLECTION,
  HISTORY_LIFETIME_SECONDS,
  MESSAGES_COLLECTION,
  createMongoConversationStore,
} = require("../src/conversations");
const { connectToDatabase } = require("../src/database");
const { describeConversationStoreContract } = require("./support/conversationStoreContract");

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
  store = createMongoConversationStore(database);
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

describe("the MongoDB conversation store against real Atlas", () => {
  describeConversationStoreContract(async () => {
    await database.collection(CONVERSATIONS_COLLECTION).deleteMany({});
    await database.collection(MESSAGES_COLLECTION).deleteMany({});
    return store;
  });

  test("conversations expire 90 days after last use and messages 90 days after they were asked", async () => {
    const expiry = async (collectionName) => {
      const indexes = await database.collection(collectionName).indexes();
      return indexes
        .filter((index) => index.expireAfterSeconds !== undefined)
        .map((index) => ({ key: index.key, seconds: index.expireAfterSeconds }));
    };

    expect(await expiry(CONVERSATIONS_COLLECTION)).toEqual([
      { key: { updated_at: 1 }, seconds: HISTORY_LIFETIME_SECONDS },
    ]);
    expect(await expiry(MESSAGES_COLLECTION)).toEqual([
      { key: { created_at: 1 }, seconds: HISTORY_LIFETIME_SECONDS },
    ]);
    expect(HISTORY_LIFETIME_SECONDS).toBe(7_776_000);
  });
});
