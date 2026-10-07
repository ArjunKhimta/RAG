const { ObjectId } = require("mongodb");

const { isObjectId } = require("../../src/repositoryLinks");

/**
 * An in-memory stand-in for `createMongoRepositoryLinkStore`, held to the same contract tests
 * (`repositoryLinkStoreContract.js`) as the real store.
 */
function createMemoryRepositoryLinkStore() {
  const records = new Map();

  function findByTargetNow(userId, repository, version) {
    return [...records.values()].find(
      (record) =>
        record.userId === userId && record.repository === repository && record.version === version,
    );
  }

  function owned(userId, linkId) {
    const record = isObjectId(linkId) ? records.get(linkId) : undefined;
    return record !== undefined && record.userId === userId ? record : undefined;
  }

  return {
    async ensureIndexes() {},

    async countForUser(userId) {
      return [...records.values()].filter((record) => record.userId === userId).length;
    },

    async findByTarget(userId, repository, version) {
      const record = findByTargetNow(userId, repository, version);
      return record === undefined ? null : { ...record };
    },

    async create({ userId, repository, version, state, now }) {
      const existing = findByTargetNow(userId, repository, version);
      if (existing !== undefined) {
        return { ...existing };
      }
      const record = {
        id: new ObjectId().toHexString(),
        userId,
        repository,
        version,
        ...state,
        createdAt: now,
        updatedAt: now,
      };
      records.set(record.id, record);
      return { ...record };
    },

    async findForUser(userId, linkId) {
      const record = owned(userId, linkId);
      return record === undefined ? null : { ...record };
    },

    async listForUser(userId) {
      return [...records.values()]
        .filter((record) => record.userId === userId)
        .sort(
          (first, second) =>
            second.createdAt - first.createdAt || second.id.localeCompare(first.id),
        )
        .map((record) => ({ ...record }));
    },

    async saveState(userId, linkId, state, now) {
      const record = owned(userId, linkId);
      if (record === undefined) {
        return null;
      }
      Object.assign(record, {
        status: state.status,
        jobId: state.jobId,
        stage: state.stage,
        progress: state.progress,
        error: state.error,
        resumeAfter: state.resumeAfter,
        indexed: state.indexed,
        updatedAt: now,
      });
      return { ...record };
    },

    async remove(userId, linkId) {
      const record = owned(userId, linkId);
      if (record === undefined) {
        return false;
      }
      records.delete(linkId);
      return true;
    },
  };
}

module.exports = { createMemoryRepositoryLinkStore };
