const { CONVERSATIONS_COLLECTION, MESSAGES_COLLECTION } = require("../src/conversations");
const { USER_DATA_COLLECTIONS } = require("../src/users");
const { describeConversationStoreContract } = require("./support/conversationStoreContract");
const { createMemoryConversationStore } = require("./support/memoryConversationStore");

describe("the in-memory conversation store used by the route tests", () => {
  describeConversationStoreContract(async () => createMemoryConversationStore());
});

test("conversations and messages live in collections that account deletion clears", () => {
  expect(USER_DATA_COLLECTIONS).toEqual(
    expect.arrayContaining([CONVERSATIONS_COLLECTION, MESSAGES_COLLECTION]),
  );
});
