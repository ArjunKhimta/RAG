/**
 * The `user_repositories` collection: which repository versions each person has added.
 *
 * A link is one person's bookmark on a repository version; the search index behind it is shared by
 * everyone and owned by the retrieval service. A link records how indexing is going as last seen
 * (its state: status, job ID, stage, progress, error, when it may resume, and, once ready, the
 * indexed commit and license), and every update replaces the whole state, so a field from an
 * earlier status never lingers.
 *
 * Every lookup is by user as well as link ID, so one person can never read, change, or remove
 * another's link. One link per person per repository version (a unique index), so two requests
 * that race to add the same one end with a single link.
 */

const { ObjectId } = require("mongodb");

const REPOSITORY_LINKS_COLLECTION = "user_repositories";

const DUPLICATE_KEY_ERROR_CODE = 11000;

const OBJECT_ID_PATTERN = /^[0-9a-f]{24}$/;

const LinkStatus = Object.freeze({
  INDEXING: "indexing",
  READY: "ready",
  WAITING_FOR_QUOTA: "waiting_for_quota",
  INTERRUPTED: "interrupted",
  FAILED: "failed",
});

/**
 * @typedef {{
 *   status: string,
 *   jobId: string | null,
 *   stage: string | null,
 *   progress: { embedded: number | null, to_embed: number | null } | null,
 *   error: { code: string, message: string } | null,
 *   resumeAfter: Date | null,
 *   indexed: { commit_id: string, license: Record<string, string> } | null,
 * }} LinkState
 *
 * @typedef {LinkState & {
 *   id: string,
 *   userId: string,
 *   repository: string,
 *   version: string,
 *   createdAt: Date,
 *   updatedAt: Date,
 * }} RepositoryLink
 */

/** @param {import("mongodb").Db} database */
function createMongoRepositoryLinkStore(database) {
  const links = database.collection(REPOSITORY_LINKS_COLLECTION);

  async function findByTarget(userId, repository, version) {
    const document = await links.findOne({ user_id: toObjectId(userId), repository, version });
    return document === null ? null : toLink(document);
  }

  return {
    async ensureIndexes() {
      await links.createIndex(
        { user_id: 1, repository: 1, version: 1 },
        { unique: true, name: "user_repository_version_unique" },
      );
      await links.createIndex({ user_id: 1, created_at: -1 }, { name: "user_newest_first" });
    },

    findByTarget,

    /**
     * Add a link, or return the one that already exists for this person and version.
     * @param {{ userId: string, repository: string, version: string, state: LinkState, now: Date }} link
     * @returns {Promise<RepositoryLink>}
     */
    async create({ userId, repository, version, state, now }) {
      const document = {
        user_id: toObjectId(userId),
        repository,
        version,
        ...toStoredState(state),
        created_at: now,
        updated_at: now,
      };
      try {
        const { insertedId } = await links.insertOne(document);
        return toLink({ ...document, _id: insertedId });
      } catch (error) {
        if (error.code === DUPLICATE_KEY_ERROR_CODE) {
          return findByTarget(userId, repository, version);
        }
        throw error;
      }
    },

    /**
     * @param {string} userId
     * @param {string} linkId
     * @returns {Promise<RepositoryLink | null>}
     */
    async findForUser(userId, linkId) {
      const filter = ownedLinkFilter(userId, linkId);
      if (filter === null) {
        return null;
      }
      const document = await links.findOne(filter);
      return document === null ? null : toLink(document);
    },

    /**
     * @param {string} userId
     * @returns {Promise<RepositoryLink[]>}
     */
    async listForUser(userId) {
      const documents = await links
        .find({ user_id: toObjectId(userId) })
        .sort({ created_at: -1, _id: -1 })
        .toArray();
      return documents.map(toLink);
    },

    /**
     * Replace a link's state; null when the person has no such link.
     * @param {string} userId
     * @param {string} linkId
     * @param {LinkState} state
     * @param {Date} now
     * @returns {Promise<RepositoryLink | null>}
     */
    async saveState(userId, linkId, state, now) {
      const filter = ownedLinkFilter(userId, linkId);
      if (filter === null) {
        return null;
      }
      const document = await links.findOneAndUpdate(
        filter,
        { $set: { ...toStoredState(state), updated_at: now } },
        { returnDocument: "after" },
      );
      return document === null ? null : toLink(document);
    },

    /**
     * Remove a link; false when the person has no such link.
     * @param {string} userId
     * @param {string} linkId
     * @returns {Promise<boolean>}
     */
    async remove(userId, linkId) {
      const filter = ownedLinkFilter(userId, linkId);
      if (filter === null) {
        return false;
      }
      const { deletedCount } = await links.deleteOne(filter);
      return deletedCount === 1;
    },
  };
}

/**
 * @param {string} userId
 * @param {string} linkId
 */
function ownedLinkFilter(userId, linkId) {
  if (!isObjectId(linkId) || !isObjectId(userId)) {
    return null;
  }
  return { _id: toObjectId(linkId), user_id: toObjectId(userId) };
}

/** @param {unknown} value */
function isObjectId(value) {
  return typeof value === "string" && OBJECT_ID_PATTERN.test(value);
}

/** @param {string} hexadecimal */
function toObjectId(hexadecimal) {
  return ObjectId.createFromHexString(hexadecimal);
}

/** @param {LinkState} state */
function toStoredState(state) {
  return {
    status: state.status,
    job_id: state.jobId,
    stage: state.stage,
    progress: state.progress,
    error: state.error,
    resume_after: state.resumeAfter,
    indexed: state.indexed,
  };
}

/**
 * @param {import("mongodb").WithId<import("mongodb").Document>} document
 * @returns {RepositoryLink}
 */
function toLink(document) {
  return {
    id: document._id.toHexString(),
    userId: document.user_id.toHexString(),
    repository: document.repository,
    version: document.version,
    status: document.status,
    jobId: document.job_id,
    stage: document.stage,
    progress: document.progress,
    error: document.error,
    resumeAfter: document.resume_after,
    indexed: document.indexed,
    createdAt: document.created_at,
    updatedAt: document.updated_at,
  };
}

module.exports = {
  LinkStatus,
  REPOSITORY_LINKS_COLLECTION,
  createMongoRepositoryLinkStore,
  isObjectId,
};
