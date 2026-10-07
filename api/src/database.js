/**
 * The connection to MongoDB Atlas. The API keeps its own collections in a separate database,
 * `code_search_app`, on the same cluster as the retrieval service's, and never reads the retrieval
 * collections; it reaches retrieval only through the service's HTTP routes.
 *
 * The connection is made once at start-up, so a wrong address or an unreachable cluster stops the
 * API at launch with a redacted message instead of failing the first request.
 */

const { MongoClient } = require("mongodb");

const APP_DATABASE_NAME = "code_search_app";

const SERVER_SELECTION_TIMEOUT_MILLISECONDS = 10_000;

const CLIENT_APP_NAME = "repositorycite-api";

/**
 * @param {string} mongodbUri
 * @param {string} [databaseName]
 * @returns {Promise<{ client: MongoClient, database: import("mongodb").Db }>}
 */
async function connectToDatabase(mongodbUri, databaseName = APP_DATABASE_NAME) {
  const client = new MongoClient(mongodbUri, {
    appName: CLIENT_APP_NAME,
    serverSelectionTimeoutMS: SERVER_SELECTION_TIMEOUT_MILLISECONDS,
  });
  await client.connect();
  return { client, database: client.db(databaseName) };
}

module.exports = { APP_DATABASE_NAME, connectToDatabase };
