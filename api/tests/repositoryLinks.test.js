const { REPOSITORY_LINKS_COLLECTION } = require("../src/repositoryLinks");
const { USER_DATA_COLLECTIONS } = require("../src/users");
const { createMemoryRepositoryLinkStore } = require("./support/memoryRepositoryLinkStore");
const { describeRepositoryLinkStoreContract } = require("./support/repositoryLinkStoreContract");

describe("the in-memory repository link store used by the route tests", () => {
  describeRepositoryLinkStoreContract(async () => createMemoryRepositoryLinkStore());
});

test("links live in a collection that account deletion clears", () => {
  expect(USER_DATA_COLLECTIONS).toContain(REPOSITORY_LINKS_COLLECTION);
});
