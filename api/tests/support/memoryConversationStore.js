const { ObjectId } = require("mongodb");

const OBJECT_ID_PATTERN = /^[0-9a-f]{24}$/;

/**
 * An in-memory stand-in for `createMongoConversationStore`, held to the same contract tests
 * (`conversationStoreContract.js`) as the real store.
 */
function createMemoryConversationStore() {
  const conversations = new Map();
  const messages = [];

  function owned(userId, conversationId) {
    const found = OBJECT_ID_PATTERN.test(String(conversationId))
      ? conversations.get(conversationId)
      : undefined;
    return found !== undefined && found.userId === userId ? found : undefined;
  }

  return {
    async ensureIndexes() {},

    async countForUser(userId) {
      return [...conversations.values()].filter((item) => item.userId === userId).length;
    },

    async create({ userId, repository, version, now }) {
      const conversation = {
        id: new ObjectId().toHexString(),
        userId,
        repository,
        version,
        title: null,
        createdAt: now,
        updatedAt: now,
      };
      conversations.set(conversation.id, conversation);
      return { ...conversation };
    },

    async listForUser(userId) {
      return [...conversations.values()]
        .filter((item) => item.userId === userId)
        .sort((first, second) => second.updatedAt - first.updatedAt || second.id.localeCompare(first.id))
        .map((item) => ({ ...item }));
    },

    async findForUser(userId, conversationId) {
      const found = owned(userId, conversationId);
      return found === undefined ? null : { ...found };
    },

    async countMessages(conversationId) {
      return messages.filter((message) => message.conversationId === conversationId).length;
    },

    async listMessages(conversationId) {
      return messages
        .filter((message) => message.conversationId === conversationId)
        .sort((first, second) => first.createdAt - second.createdAt || first.id.localeCompare(second.id))
        .map(withoutOwner);
    },

    async addMessage({ userId, conversationId, question, excludeTests, reply, title, now }) {
      const message = {
        id: new ObjectId().toHexString(),
        userId,
        conversationId,
        question,
        excludeTests,
        reply,
        createdAt: now,
      };
      messages.push(message);
      const conversation = owned(userId, conversationId);
      conversation.updatedAt = now;
      if (conversation.title === null) {
        conversation.title = title;
      }
      return withoutOwner(message);
    },

    async remove(userId, conversationId) {
      if (owned(userId, conversationId) === undefined) {
        return false;
      }
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        if (messages[index].conversationId === conversationId) {
          messages.splice(index, 1);
        }
      }
      conversations.delete(conversationId);
      return true;
    },
  };
}

function withoutOwner(message) {
  return {
    id: message.id,
    conversationId: message.conversationId,
    question: message.question,
    excludeTests: message.excludeTests,
    reply: message.reply,
    createdAt: message.createdAt,
  };
}

module.exports = { createMemoryConversationStore };
