const crypto = require("node:crypto");

const { loadEnvironmentFile, readDatabaseSettings } = require("../src/config");
const { connectToDatabase } = require("../src/database");
const {
  USERS_COLLECTION,
  USER_DATA_COLLECTIONS,
  createMongoUserStore,
} = require("../src/users");
const { ObjectId } = require("mongodb");
const { describeUserStoreContract } = require("./support/userStoreContract");
const { emptyDatabase } = require("./support/testDatabases");

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
  store = createMongoUserStore(database);
  await store.ensureIndexes();
});

afterAll(async () => {
  if (database !== undefined) {
    await emptyDatabase(database);
  }
  if (client !== undefined) {
    await client.close();
  }
});

describe("the MongoDB users store against real Atlas", () => {
  describeUserStoreContract(async () => {
    for (const collectionName of [USERS_COLLECTION, ...USER_DATA_COLLECTIONS]) {
      await database.collection(collectionName).deleteMany({});
    }
    return {
      store,
      addUserData: async (collectionName, userId) => {
        await database
          .collection(collectionName)
          .insertOne({ user_id: ObjectId.createFromHexString(userId) });
      },
      countUserData: (collectionName, userId) =>
        database
          .collection(collectionName)
          .countDocuments({ user_id: ObjectId.createFromHexString(userId) }),
    };
  });

  test("two sign-ins at once for a new GitHub account make one user", async () => {
    await database.collection(USERS_COLLECTION).deleteMany({});
    const profile = {
      githubId: 9_000_001,
      login: "racer",
      name: null,
      avatarUrl: "https://avatars.githubusercontent.com/u/9000001?v=4",
    };
    const now = new Date();

    const users = await Promise.all([
      store.saveSignedInUser(profile, now),
      store.saveSignedInUser(profile, now),
    ]);

    expect(users[0].id).toBe(users[1].id);
    expect(await database.collection(USERS_COLLECTION).countDocuments({})).toBe(1);
  });

  test("dates are stored as dates and no token or email field exists", async () => {
    await database.collection(USERS_COLLECTION).deleteMany({});
    const now = new Date("2026-10-07T12:00:00Z");
    const user = await store.saveSignedInUser(
      { githubId: 9_000_002, login: "stored", name: "Stored", avatarUrl: "https://example.com/a" },
      now,
    );

    const document = await database
      .collection(USERS_COLLECTION)
      .findOne({ _id: ObjectId.createFromHexString(user.id) });

    expect(Object.keys(document).sort()).toEqual([
      "_id",
      "avatar_url",
      "created_at",
      "github_id",
      "last_signed_in_at",
      "login",
      "name",
      "session_version",
      "updated_at",
    ]);
    expect(document.created_at).toEqual(now);
  });
});
