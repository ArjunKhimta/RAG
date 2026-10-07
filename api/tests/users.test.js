const { USER_DATA_COLLECTIONS } = require("../src/users");
const { createMemoryUserStore } = require("./support/memoryUserStore");
const { describeUserStoreContract } = require("./support/userStoreContract");

describe("the in-memory users store used by the route tests", () => {
  describeUserStoreContract(async () => {
    const store = createMemoryUserStore();
    return {
      store,
      addUserData: store.addUserData,
      countUserData: store.countUserData,
    };
  });
});

test("account deletion covers every collection planned to hold people's data", () => {
  expect(USER_DATA_COLLECTIONS).toEqual([
    "user_repositories",
    "conversations",
    "messages",
    "usage",
  ]);
});
