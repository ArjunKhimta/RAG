const { ObjectId } = require("mongodb");

const { FIRST_SESSION_VERSION, USER_DATA_COLLECTIONS } = require("../../src/users");

/**
 * An in-memory stand-in for `createMongoUserStore`, held to the same contract tests
 * (`userStoreContract.js`) as the real store, plus helpers to plant and count a user's records in
 * the user-data collections.
 */
function createMemoryUserStore() {
  const records = new Map();
  const userData = new Map(USER_DATA_COLLECTIONS.map((name) => [name, []]));

  function findByGithubId(githubId) {
    return [...records.values()].find((record) => record.githubId === githubId);
  }

  return {
    async ensureIndexes() {},

    async saveSignedInUser(profile, now) {
      let record = findByGithubId(profile.githubId);
      if (record === undefined) {
        record = {
          id: new ObjectId().toHexString(),
          githubId: profile.githubId,
          sessionVersion: FIRST_SESSION_VERSION,
          createdAt: now,
        };
        records.set(record.id, record);
      }
      Object.assign(record, {
        login: profile.login,
        name: profile.name,
        avatarUrl: profile.avatarUrl,
        updatedAt: now,
        lastSignedInAt: now,
      });
      return toUser(record);
    },

    async findById(userId) {
      const record = records.get(userId);
      return record === undefined ? null : toUser(record);
    },

    async endAllSessions(userId, now) {
      const record = records.get(userId);
      if (record !== undefined) {
        record.sessionVersion += 1;
        record.updatedAt = now;
      }
    },

    async deleteAccount(userId) {
      for (const [name, owners] of userData) {
        userData.set(name, owners.filter((owner) => owner !== userId));
      }
      records.delete(userId);
    },

    addUserData(collectionName, userId) {
      userData.get(collectionName).push(userId);
    },

    countUserData(collectionName, userId) {
      return userData.get(collectionName).filter((owner) => owner === userId).length;
    },
  };
}

function toUser(record) {
  return {
    id: record.id,
    githubId: record.githubId,
    login: record.login,
    name: record.name,
    avatarUrl: record.avatarUrl,
    sessionVersion: record.sessionVersion,
  };
}

module.exports = { createMemoryUserStore };
