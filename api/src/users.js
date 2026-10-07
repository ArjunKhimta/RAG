/**
 * The `users` collection in the API's own database (`code_search_app`), which the retrieval service
 * never reads.
 *
 * One record per GitHub account, found by GitHub's numeric ID, which never changes (a login name
 * can). It keeps only what the site shows (login, name, avatar), when the account was created and
 * last signed in, and `session_version`, which signing out raises to cancel every session token
 * issued before. No email address and no GitHub token is ever stored.
 *
 * Deleting an account removes everything the API keeps about the person: first their records in
 * every collection listed in `USER_DATA_COLLECTIONS` (each holds a `user_id`), then the user record
 * itself, so a deletion that fails partway can simply be asked for again. A collection that holds
 * people's data must be added to that list when it is created.
 */

const { ObjectId } = require("mongodb");

const USERS_COLLECTION = "users";

const USER_DATA_COLLECTIONS = ["user_repositories", "conversations", "messages", "usage"];

const FIRST_SESSION_VERSION = 1;

const USER_ID_PATTERN = /^[0-9a-f]{24}$/;

/**
 * @typedef {{
 *   id: string,
 *   githubId: number,
 *   login: string,
 *   name: string | null,
 *   avatarUrl: string,
 *   sessionVersion: number,
 * }} User
 */

/** @param {import("mongodb").Db} database */
function createMongoUserStore(database) {
  const users = database.collection(USERS_COLLECTION);

  return {
    async ensureIndexes() {
      await users.createIndex({ github_id: 1 }, { unique: true, name: "github_id_unique" });
    },

    /**
     * Create or refresh the record for a GitHub account that has just signed in.
     * @param {{ githubId: number, login: string, name: string | null, avatarUrl: string }} profile
     * @param {Date} now
     * @returns {Promise<User>}
     */
    async saveSignedInUser(profile, now) {
      const document = await users.findOneAndUpdate(
        { github_id: profile.githubId },
        {
          $set: {
            login: profile.login,
            name: profile.name,
            avatar_url: profile.avatarUrl,
            updated_at: now,
            last_signed_in_at: now,
          },
          $setOnInsert: {
            github_id: profile.githubId,
            session_version: FIRST_SESSION_VERSION,
            created_at: now,
          },
        },
        { upsert: true, returnDocument: "after" },
      );
      return toUser(document);
    },

    /**
     * @param {string} userId
     * @returns {Promise<User | null>}
     */
    async findById(userId) {
      const objectId = toObjectId(userId);
      if (objectId === null) {
        return null;
      }
      const document = await users.findOne({ _id: objectId });
      return document === null ? null : toUser(document);
    },

    /**
     * Cancel every session token issued to the user so far.
     * @param {string} userId
     * @param {Date} now
     */
    async endAllSessions(userId, now) {
      const objectId = toObjectId(userId);
      if (objectId === null) {
        return;
      }
      await users.updateOne(
        { _id: objectId },
        { $inc: { session_version: 1 }, $set: { updated_at: now } },
      );
    },

    /**
     * Delete the user's records in every user-data collection, then the user record.
     * @param {string} userId
     */
    async deleteAccount(userId) {
      const objectId = toObjectId(userId);
      if (objectId === null) {
        return;
      }
      for (const collectionName of USER_DATA_COLLECTIONS) {
        await database.collection(collectionName).deleteMany({ user_id: objectId });
      }
      await users.deleteOne({ _id: objectId });
    },
  };
}

/** @param {string} userId */
function toObjectId(userId) {
  if (typeof userId !== "string" || !USER_ID_PATTERN.test(userId)) {
    return null;
  }
  return ObjectId.createFromHexString(userId);
}

/**
 * @param {import("mongodb").WithId<import("mongodb").Document>} document
 * @returns {User}
 */
function toUser(document) {
  return {
    id: document._id.toHexString(),
    githubId: document.github_id,
    login: document.login,
    name: document.name,
    avatarUrl: document.avatar_url,
    sessionVersion: document.session_version,
  };
}

module.exports = {
  USERS_COLLECTION,
  USER_DATA_COLLECTIONS,
  FIRST_SESSION_VERSION,
  createMongoUserStore,
};
