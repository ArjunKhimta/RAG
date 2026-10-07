const { UsageKind } = require("../../src/usage");

const FIRST_USER = "0123456789abcdef01234567";

const SECOND_USER = "fedcba9876543210fedcba98";

const TODAY = "2026-10-07";

const TOMORROW = "2026-10-08";

const NOW = new Date("2026-10-07T12:00:00Z");

/**
 * The behaviour every usage store must have. `setUp` runs before each test and returns an empty
 * store.
 * @param {() => Promise<any>} setUp
 */
function describeUsageStoreContract(setUp) {
  let store;

  beforeEach(async () => {
    store = await setUp();
  });

  function book(overrides = {}) {
    return store.book({
      userId: FIRST_USER,
      kind: UsageKind.QUESTIONS,
      limit: 3,
      day: TODAY,
      now: NOW,
      ...overrides,
    });
  }

  test("nothing used reads as zeros", async () => {
    expect(await store.read(FIRST_USER, TODAY)).toEqual({
      questions: 0,
      searches: 0,
      indexing_requests: 0,
    });
  });

  test("slots are booked up to the limit and no further", async () => {
    const results = [await book(), await book(), await book(), await book()];

    expect(results).toEqual([true, true, true, false]);
    expect((await store.read(FIRST_USER, TODAY)).questions).toBe(3);
  });

  test("each kind, person, and day counts on its own", async () => {
    await book();
    await book();
    await book();

    expect(await book({ kind: UsageKind.SEARCHES })).toBe(true);
    expect(await book({ userId: SECOND_USER })).toBe(true);
    expect(await book({ day: TOMORROW })).toBe(true);
    expect(await store.read(FIRST_USER, TODAY)).toEqual({
      questions: 3,
      searches: 1,
      indexing_requests: 0,
    });
  });

  test("a slot given back can be booked again, and counts never go below zero", async () => {
    await book();
    await book();
    await book();

    await store.giveBack({ userId: FIRST_USER, kind: UsageKind.QUESTIONS, day: TODAY });
    expect(await book()).toBe(true);

    for (let index = 0; index < 5; index += 1) {
      await store.giveBack({ userId: FIRST_USER, kind: UsageKind.SEARCHES, day: TODAY });
    }
    expect((await store.read(FIRST_USER, TODAY)).searches).toBe(0);
  });

  test("giving back on another day leaves today alone", async () => {
    await book();

    await store.giveBack({ userId: FIRST_USER, kind: UsageKind.QUESTIONS, day: TOMORROW });

    expect((await store.read(FIRST_USER, TODAY)).questions).toBe(1);
  });
}

module.exports = { FIRST_USER, NOW, TODAY, describeUsageStoreContract };
