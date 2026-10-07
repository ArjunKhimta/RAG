const { DEFAULT_LIMITS, withLimits } = require("../src/limits");
const { USER_DATA_COLLECTIONS } = require("../src/users");
const {
  USAGE_COLLECTION,
  UsageKind,
  createDailyLimits,
  dailyLimitMessage,
} = require("../src/usage");
const { createClock } = require("./support/testApp");
const { createMemoryUsageStore } = require("./support/memoryUsageStore");
const { FIRST_USER, describeUsageStoreContract } = require("./support/usageStoreContract");

describe("the in-memory usage store used by the route tests", () => {
  describeUsageStoreContract(async () => createMemoryUsageStore());
});

test("usage lives in a collection that account deletion clears", () => {
  expect(USER_DATA_COLLECTIONS).toContain(USAGE_COLLECTION);
});

test("the starting limits are the approved values", () => {
  expect(DEFAULT_LIMITS).toEqual({
    questionsPerDay: 30,
    questionsPerMinute: 5,
    searchesPerDay: 100,
    indexingRequestsPerDay: 2,
    linkedRepositories: 5,
    conversationsPerUser: 50,
    messagesPerConversation: 100,
    signInsPerWindow: 20,
    signInWindowMinutes: 15,
  });
});

describe("daily limits", () => {
  const AFTERNOON_PACIFIC = Date.parse("2026-10-07T20:30:00Z");

  function build(overrides = { questionsPerDay: 2 }) {
    const clock = createClock(AFTERNOON_PACIFIC);
    const usage = createMemoryUsageStore();
    const limits = createDailyLimits({ usage, limits: withLimits(overrides), now: clock.now });
    return { clock, usage, limits };
  }

  test("past the limit it is a 429 naming the limit, with seconds until midnight Pacific", async () => {
    const { limits } = build();
    await limits.take(FIRST_USER, UsageKind.QUESTIONS);
    await limits.take(FIRST_USER, UsageKind.QUESTIONS);

    const refusal = await limits.take(FIRST_USER, UsageKind.QUESTIONS).catch((error) => error);

    expect(refusal.status).toBe(429);
    expect(refusal.message).toBe(dailyLimitMessage(UsageKind.QUESTIONS, 2));
    expect(refusal.message).toBe(
      "You have used today's 2 questions; the limit resets at midnight Pacific time",
    );
    expect(refusal.retryAfterSeconds).toBe(37_800);
  });

  test("a new Pacific day brings a fresh allowance", async () => {
    const { clock, limits } = build();
    await limits.take(FIRST_USER, UsageKind.QUESTIONS);
    await limits.take(FIRST_USER, UsageKind.QUESTIONS);

    clock.advanceSeconds(37_800);

    await expect(limits.take(FIRST_USER, UsageKind.QUESTIONS)).resolves.toMatchObject({
      day: "2026-10-08",
    });
  });

  test("a slot taken before midnight is given back to the day it came from", async () => {
    const { clock, usage, limits } = build();
    const ticket = await limits.take(FIRST_USER, UsageKind.QUESTIONS);
    clock.advanceSeconds(37_800);

    await limits.giveBack(ticket);

    expect((await usage.read(FIRST_USER, "2026-10-07")).questions).toBe(0);
  });

  test("a failure to give back is logged, never thrown", async () => {
    const { limits, usage } = build();
    const ticket = await limits.take(FIRST_USER, UsageKind.QUESTIONS);
    usage.giveBack = async () => {
      throw new Error("database unreachable");
    };
    const errorLog = jest.spyOn(console, "error").mockImplementation(() => {});

    await expect(limits.giveBack(ticket)).resolves.toBeUndefined();

    expect(errorLog.mock.calls.join("\n")).toContain("database unreachable");
    errorLog.mockRestore();
  });

  test("the report shows each kind's use and limit, and when they reset", async () => {
    const { limits } = build(DEFAULT_LIMITS);
    await limits.take(FIRST_USER, UsageKind.SEARCHES);

    const report = await limits.report(FIRST_USER);

    expect(report).toEqual({
      day: "2026-10-07",
      resetsAt: new Date("2026-10-08T07:00:00Z"),
      counts: {
        questions: { used: 0, limit: 30 },
        searches: { used: 1, limit: 100 },
        indexing_requests: { used: 0, limit: 2 },
      },
    });
  });
});
