const { FIRST_SESSION_VERSION, USER_DATA_COLLECTIONS } = require("../../src/users");

const SIGN_IN_TIME = new Date("2026-10-07T12:00:00Z");

const LATER_TIME = new Date("2026-10-08T12:00:00Z");

const OCTOCAT = {
  githubId: 583231,
  login: "octocat",
  name: "The Octocat",
  avatarUrl: "https://avatars.githubusercontent.com/u/583231?v=4",
};

const HUBOT = {
  githubId: 480938,
  login: "hubot",
  name: null,
  avatarUrl: "https://avatars.githubusercontent.com/u/480938?v=4",
};

/**
 * The behaviour every users store must have. `setUp` runs before each test and returns the store
 * and two helpers that plant and count a user's records in a user-data collection.
 * @param {() => Promise<{
 *   store: any,
 *   addUserData(collectionName: string, userId: string): Promise<void> | void,
 *   countUserData(collectionName: string, userId: string): Promise<number> | number,
 * }>} setUp
 */
function describeUserStoreContract(setUp) {
  let store;
  let addUserData;
  let countUserData;

  beforeEach(async () => {
    ({ store, addUserData, countUserData } = await setUp());
  });

  test("a first sign-in creates a user at the first session version", async () => {
    const user = await store.saveSignedInUser(OCTOCAT, SIGN_IN_TIME);

    expect(user).toEqual({
      id: expect.stringMatching(/^[0-9a-f]{24}$/),
      githubId: OCTOCAT.githubId,
      login: OCTOCAT.login,
      name: OCTOCAT.name,
      avatarUrl: OCTOCAT.avatarUrl,
      sessionVersion: FIRST_SESSION_VERSION,
    });
    expect(await store.findById(user.id)).toEqual(user);
  });

  test("signing in again refreshes the profile and keeps the ID and session version", async () => {
    const first = await store.saveSignedInUser(OCTOCAT, SIGN_IN_TIME);
    await store.endAllSessions(first.id, SIGN_IN_TIME);
    const renamed = { ...OCTOCAT, login: "octocat-renamed", name: null };

    const second = await store.saveSignedInUser(renamed, LATER_TIME);

    expect(second).toEqual({
      ...first,
      login: "octocat-renamed",
      name: null,
      sessionVersion: FIRST_SESSION_VERSION + 1,
    });
  });

  test("different GitHub accounts get different users", async () => {
    const octocat = await store.saveSignedInUser(OCTOCAT, SIGN_IN_TIME);
    const hubot = await store.saveSignedInUser(HUBOT, SIGN_IN_TIME);

    expect(hubot.id).not.toBe(octocat.id);
    expect(hubot.name).toBeNull();
  });

  test.each(["0123456789abcdef01234567", "not-an-id", ""])(
    "finding user %p that does not exist gives null",
    async (userId) => {
      expect(await store.findById(userId)).toBeNull();
    },
  );

  test("ending all sessions raises only that user's session version, once per call", async () => {
    const octocat = await store.saveSignedInUser(OCTOCAT, SIGN_IN_TIME);
    const hubot = await store.saveSignedInUser(HUBOT, SIGN_IN_TIME);

    await store.endAllSessions(octocat.id, LATER_TIME);
    await store.endAllSessions(octocat.id, LATER_TIME);

    expect((await store.findById(octocat.id)).sessionVersion).toBe(FIRST_SESSION_VERSION + 2);
    expect((await store.findById(hubot.id)).sessionVersion).toBe(FIRST_SESSION_VERSION);
  });

  test("deleting an account removes the user and their records in every user-data collection, and no one else's", async () => {
    const octocat = await store.saveSignedInUser(OCTOCAT, SIGN_IN_TIME);
    const hubot = await store.saveSignedInUser(HUBOT, SIGN_IN_TIME);
    for (const collectionName of USER_DATA_COLLECTIONS) {
      await addUserData(collectionName, octocat.id);
      await addUserData(collectionName, octocat.id);
      await addUserData(collectionName, hubot.id);
    }

    await store.deleteAccount(octocat.id);

    expect(await store.findById(octocat.id)).toBeNull();
    expect(await store.findById(hubot.id)).toEqual(hubot);
    for (const collectionName of USER_DATA_COLLECTIONS) {
      expect(await countUserData(collectionName, octocat.id)).toBe(0);
      expect(await countUserData(collectionName, hubot.id)).toBe(1);
    }
  });

  test.each(["0123456789abcdef01234567", "not-an-id"])(
    "deleting or signing out user %p that does not exist does nothing",
    async (userId) => {
      const octocat = await store.saveSignedInUser(OCTOCAT, SIGN_IN_TIME);

      await store.deleteAccount(userId);
      await store.endAllSessions(userId, LATER_TIME);

      expect(await store.findById(octocat.id)).toEqual(octocat);
    },
  );
}

module.exports = { HUBOT, OCTOCAT, SIGN_IN_TIME, describeUserStoreContract };
