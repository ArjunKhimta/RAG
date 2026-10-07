const crypto = require("node:crypto");

const { loadEnvironmentFile, readDatabaseSettings } = require("../src/config");
const { connectToDatabase } = require("../src/database");
const { indexingState } = require("../src/linkStates");
const {
  REPOSITORY_LINKS_COLLECTION,
  createMongoRepositoryLinkStore,
} = require("../src/repositoryLinks");
const {
  FIRST_USER,
  describeRepositoryLinkStoreContract,
} = require("./support/repositoryLinkStoreContract");

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
  store = createMongoRepositoryLinkStore(database);
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
  await database.collection(REPOSITORY_LINKS_COLLECTION).deleteMany({});
  return store;
}

describe("the MongoDB repository link store against real Atlas", () => {
  describeRepositoryLinkStoreContract(emptyStore);

  test("ten requests at once to add the same version make one link", async () => {
    await emptyStore();
    const now = new Date();

    const links = await Promise.all(
      Array.from({ length: 10 }, () =>
        store.create({
          userId: FIRST_USER,
          repository: "pallets/flask",
          version: "3.1.3",
          state: indexingState("0123456789abcdef0123456789abcdef"),
          now,
        }),
      ),
    );

    expect(new Set(links.map((link) => link.id)).size).toBe(1);
    expect(await database.collection(REPOSITORY_LINKS_COLLECTION).countDocuments({})).toBe(1);
  });
});
