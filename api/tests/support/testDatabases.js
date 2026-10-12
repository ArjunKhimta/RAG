/**
 * Emptying a throwaway test database without the right to drop a database.
 *
 * The local Atlas user may read and write any database but not drop one (lowered from Atlas admin
 * on 2026-10-12, so the key on the laptop cannot manage users, network access, or the cluster).
 * Tests delete every collection instead; MongoDB removes a database once it holds no collections.
 */

const SYSTEM_COLLECTION_PREFIX = "system.";

/** @param {import("mongodb").Db} database */
async function emptyDatabase(database) {
  const collections = await database.listCollections({}, { nameOnly: true }).toArray();
  for (const { name } of collections) {
    if (!name.startsWith(SYSTEM_COLLECTION_PREFIX)) {
      await database.dropCollection(name);
    }
  }
}

module.exports = { emptyDatabase };
