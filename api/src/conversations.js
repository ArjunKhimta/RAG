/**
 * Chat history: the `conversations` and `messages` collections.
 *
 * A conversation is about one repository version, so every answer in it comes from one commit
 * under one license. A message is one question and the whole reply the person saw (sentences,
 * sources, snippets, license, usage, timings), so reopening a conversation shows exactly what was
 * shown before without asking again. Questions are answered independently; earlier messages are a
 * record, never context for the next answer.
 *
 * History is kept for 90 days: MongoDB's TTL indexes delete each message 90 days after it was
 * asked and each conversation 90 days after it was last used. Every lookup is by user as well as
 * ID. Deleting a conversation removes its messages first, so a deletion that fails partway leaves
 * an empty conversation that can be deleted again rather than messages with no conversation.
 */

const { ObjectId } = require("mongodb");

const CONVERSATIONS_COLLECTION = "conversations";

const MESSAGES_COLLECTION = "messages";

const HISTORY_LIFETIME_SECONDS = 90 * 24 * 60 * 60;

const OBJECT_ID_PATTERN = /^[0-9a-f]{24}$/;

/**
 * @typedef {{
 *   id: string,
 *   userId: string,
 *   repository: string,
 *   version: string,
 *   title: string | null,
 *   createdAt: Date,
 *   updatedAt: Date,
 * }} Conversation
 *
 * @typedef {{
 *   id: string,
 *   conversationId: string,
 *   question: string,
 *   excludeTests: boolean,
 *   reply: Record<string, unknown>,
 *   createdAt: Date,
 * }} Message
 */

/** @param {import("mongodb").Db} database */
function createMongoConversationStore(database) {
  const conversations = database.collection(CONVERSATIONS_COLLECTION);
  const messages = database.collection(MESSAGES_COLLECTION);

  return {
    async ensureIndexes() {
      await conversations.createIndex({ user_id: 1, updated_at: -1 }, { name: "user_recent_first" });
      await conversations.createIndex(
        { updated_at: 1 },
        { expireAfterSeconds: HISTORY_LIFETIME_SECONDS, name: "expire_unused" },
      );
      await messages.createIndex(
        { conversation_id: 1, created_at: 1 },
        { name: "conversation_oldest_first" },
      );
      await messages.createIndex({ user_id: 1 }, { name: "user" });
      await messages.createIndex(
        { created_at: 1 },
        { expireAfterSeconds: HISTORY_LIFETIME_SECONDS, name: "expire_old" },
      );
    },

    /** @param {string} userId */
    countForUser(userId) {
      return conversations.countDocuments({ user_id: toObjectId(userId) });
    },

    /**
     * @param {{ userId: string, repository: string, version: string, now: Date }} conversation
     * @returns {Promise<Conversation>}
     */
    async create({ userId, repository, version, now }) {
      const document = {
        user_id: toObjectId(userId),
        repository,
        version,
        title: null,
        created_at: now,
        updated_at: now,
      };
      const { insertedId } = await conversations.insertOne(document);
      return toConversation({ ...document, _id: insertedId });
    },

    /**
     * @param {string} userId
     * @returns {Promise<Conversation[]>}
     */
    async listForUser(userId) {
      const documents = await conversations
        .find({ user_id: toObjectId(userId) })
        .sort({ updated_at: -1, _id: -1 })
        .toArray();
      return documents.map(toConversation);
    },

    /**
     * @param {string} userId
     * @param {string} conversationId
     * @returns {Promise<Conversation | null>}
     */
    async findForUser(userId, conversationId) {
      const filter = ownedFilter(userId, conversationId);
      if (filter === null) {
        return null;
      }
      const document = await conversations.findOne(filter);
      return document === null ? null : toConversation(document);
    },

    /** @param {string} conversationId */
    countMessages(conversationId) {
      return messages.countDocuments({ conversation_id: toObjectId(conversationId) });
    },

    /**
     * @param {string} conversationId
     * @returns {Promise<Message[]>}
     */
    async listMessages(conversationId) {
      const documents = await messages
        .find({ conversation_id: toObjectId(conversationId) })
        .sort({ created_at: 1, _id: 1 })
        .toArray();
      return documents.map(toMessage);
    },

    /**
     * Save a question and its reply, mark the conversation used, and give it the question as its
     * title if it has none.
     * @param {{
     *   userId: string,
     *   conversationId: string,
     *   question: string,
     *   excludeTests: boolean,
     *   reply: Record<string, unknown>,
     *   title: string,
     *   now: Date,
     * }} message
     * @returns {Promise<Message>}
     */
    async addMessage({ userId, conversationId, question, excludeTests, reply, title, now }) {
      const document = {
        user_id: toObjectId(userId),
        conversation_id: toObjectId(conversationId),
        question,
        exclude_tests: excludeTests,
        reply,
        created_at: now,
      };
      const { insertedId } = await messages.insertOne(document);
      const owned = ownedFilter(userId, conversationId);
      await conversations.updateOne(owned, { $set: { updated_at: now } });
      await conversations.updateOne({ ...owned, title: null }, { $set: { title } });
      return toMessage({ ...document, _id: insertedId });
    },

    /**
     * Delete a conversation and its messages; false when the person has no such conversation.
     * @param {string} userId
     * @param {string} conversationId
     * @returns {Promise<boolean>}
     */
    async remove(userId, conversationId) {
      const filter = ownedFilter(userId, conversationId);
      if (filter === null || (await conversations.countDocuments(filter)) === 0) {
        return false;
      }
      await messages.deleteMany({ conversation_id: filter._id, user_id: filter.user_id });
      await conversations.deleteOne(filter);
      return true;
    },
  };
}

/**
 * @param {string} userId
 * @param {string} conversationId
 */
function ownedFilter(userId, conversationId) {
  if (!isObjectId(conversationId) || !isObjectId(userId)) {
    return null;
  }
  return { _id: toObjectId(conversationId), user_id: toObjectId(userId) };
}

/** @param {unknown} value */
function isObjectId(value) {
  return typeof value === "string" && OBJECT_ID_PATTERN.test(value);
}

/** @param {string} hexadecimal */
function toObjectId(hexadecimal) {
  return ObjectId.createFromHexString(hexadecimal);
}

/** @returns {Conversation} */
function toConversation(document) {
  return {
    id: document._id.toHexString(),
    userId: document.user_id.toHexString(),
    repository: document.repository,
    version: document.version,
    title: document.title,
    createdAt: document.created_at,
    updatedAt: document.updated_at,
  };
}

/** @returns {Message} */
function toMessage(document) {
  return {
    id: document._id.toHexString(),
    conversationId: document.conversation_id.toHexString(),
    question: document.question,
    excludeTests: document.exclude_tests,
    reply: document.reply,
    createdAt: document.created_at,
  };
}

module.exports = {
  CONVERSATIONS_COLLECTION,
  HISTORY_LIFETIME_SECONDS,
  MESSAGES_COLLECTION,
  createMongoConversationStore,
};
