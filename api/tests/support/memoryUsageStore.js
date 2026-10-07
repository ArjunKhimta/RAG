const { UsageKind } = require("../../src/usage");

/**
 * An in-memory stand-in for `createMongoUsageStore`, held to the same contract tests
 * (`usageStoreContract.js`) as the real store.
 */
function createMemoryUsageStore() {
  const days = new Map();

  function countsFor(userId, day) {
    const key = `${userId}:${day}`;
    if (!days.has(key)) {
      days.set(key, {
        [UsageKind.QUESTIONS]: 0,
        [UsageKind.SEARCHES]: 0,
        [UsageKind.INDEXING_REQUESTS]: 0,
      });
    }
    return days.get(key);
  }

  return {
    async ensureIndexes() {},

    async book({ userId, kind, limit, day }) {
      const counts = countsFor(userId, day);
      if (counts[kind] >= limit) {
        return false;
      }
      counts[kind] += 1;
      return true;
    },

    async giveBack({ userId, kind, day }) {
      const counts = countsFor(userId, day);
      counts[kind] = Math.max(0, counts[kind] - 1);
    },

    async read(userId, day) {
      return { ...countsFor(userId, day) };
    },

    setCount(userId, day, kind, count) {
      countsFor(userId, day)[kind] = count;
    },
  };
}

module.exports = { createMemoryUsageStore };
