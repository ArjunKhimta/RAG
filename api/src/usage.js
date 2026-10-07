/**
 * Daily per-person counts in the `usage` collection: one document per person per Pacific day
 * holding `questions`, `searches`, and `indexing_requests`, deleted 30 days after it was created.
 * It holds counts only, never what was asked.
 *
 * Taking a slot is two steps, as in the retrieval service's embedding budget: make sure today's
 * document exists (an upsert that sets zeros only when it creates it), then add one only if the
 * count is still under the limit, in a single conditional update. MongoDB applies each update to a
 * document on its own, so two requests racing for the last slot cannot both get it.
 *
 * A slot is given back when the service refuses before Gemini did any work, so a person never
 * pays for a busy service or a bad request; it is taken from the day it was booked, even after
 * midnight, and never below zero.
 */

const { ObjectId } = require("mongodb");

const { ApiError } = require("./apiError");
const { logError } = require("./logging");
const { nextPacificMidnight, pacificDay, secondsUntilPacificMidnight } = require("./pacificDay");
const { redactError } = require("./redaction");

const USAGE_COLLECTION = "usage";

const USAGE_LIFETIME_SECONDS = 30 * 24 * 60 * 60;

const DUPLICATE_KEY_ERROR_CODE = 11000;

const TOO_MANY_REQUESTS_STATUS = 429;

const UsageKind = Object.freeze({
  QUESTIONS: "questions",
  SEARCHES: "searches",
  INDEXING_REQUESTS: "indexing_requests",
});

const KIND_LABELS = Object.freeze({
  [UsageKind.QUESTIONS]: "questions",
  [UsageKind.SEARCHES]: "searches",
  [UsageKind.INDEXING_REQUESTS]: "new repositories to index",
});

/** @param {import("mongodb").Db} database */
function createMongoUsageStore(database) {
  const usage = database.collection(USAGE_COLLECTION);

  async function ensureDay(userId, day, now) {
    try {
      await usage.updateOne(
        { user_id: toObjectId(userId), day },
        {
          $setOnInsert: {
            [UsageKind.QUESTIONS]: 0,
            [UsageKind.SEARCHES]: 0,
            [UsageKind.INDEXING_REQUESTS]: 0,
            created_at: now,
          },
        },
        { upsert: true },
      );
    } catch (error) {
      if (error.code !== DUPLICATE_KEY_ERROR_CODE) {
        throw error;
      }
    }
  }

  return {
    async ensureIndexes() {
      await usage.createIndex({ user_id: 1, day: 1 }, { unique: true, name: "user_day_unique" });
      await usage.createIndex(
        { created_at: 1 },
        { expireAfterSeconds: USAGE_LIFETIME_SECONDS, name: "expire_old" },
      );
    },

    /**
     * Add one to the day's count if it is under `limit`; whether it was added.
     * @param {{ userId: string, kind: string, limit: number, day: string, now: Date }} booking
     */
    async book({ userId, kind, limit, day, now }) {
      await ensureDay(userId, day, now);
      const result = await usage.updateOne(
        { user_id: toObjectId(userId), day, [kind]: { $lt: limit } },
        { $inc: { [kind]: 1 } },
      );
      return result.modifiedCount === 1;
    },

    /** @param {{ userId: string, kind: string, day: string }} booking */
    async giveBack({ userId, kind, day }) {
      await usage.updateOne(
        { user_id: toObjectId(userId), day, [kind]: { $gt: 0 } },
        { $inc: { [kind]: -1 } },
      );
    },

    /**
     * The day's counts, zero when nothing was used.
     * @param {string} userId
     * @param {string} day
     */
    async read(userId, day) {
      const document = await usage.findOne({ user_id: toObjectId(userId), day });
      return {
        [UsageKind.QUESTIONS]: document?.[UsageKind.QUESTIONS] ?? 0,
        [UsageKind.SEARCHES]: document?.[UsageKind.SEARCHES] ?? 0,
        [UsageKind.INDEXING_REQUESTS]: document?.[UsageKind.INDEXING_REQUESTS] ?? 0,
      };
    },
  };
}

/**
 * Daily limits over a usage store.
 * @param {{
 *   usage: ReturnType<typeof createMongoUsageStore>,
 *   limits: typeof import("./limits").DEFAULT_LIMITS,
 *   now?: () => number,
 * }} dependencies
 */
function createDailyLimits({ usage, limits, now = Date.now }) {
  const dailyLimit = {
    [UsageKind.QUESTIONS]: limits.questionsPerDay,
    [UsageKind.SEARCHES]: limits.searchesPerDay,
    [UsageKind.INDEXING_REQUESTS]: limits.indexingRequestsPerDay,
  };

  return {
    /**
     * Take one of today's slots, or refuse with 429 and the seconds until midnight Pacific.
     * @param {string} userId
     * @param {string} kind
     * @returns {Promise<{ userId: string, kind: string, day: string }>}
     */
    async take(userId, kind) {
      const nowMilliseconds = now();
      const day = pacificDay(nowMilliseconds);
      const limit = dailyLimit[kind];
      const booked = await usage.book({
        userId,
        kind,
        limit,
        day,
        now: new Date(nowMilliseconds),
      });
      if (!booked) {
        throw new ApiError(
          TOO_MANY_REQUESTS_STATUS,
          dailyLimitMessage(kind, limit),
          secondsUntilPacificMidnight(nowMilliseconds),
        );
      }
      return { userId, kind, day };
    },

    /**
     * Return a slot; a failure is logged and never hides the error that led here.
     * @param {{ userId: string, kind: string, day: string }} ticket
     */
    async giveBack(ticket) {
      try {
        await usage.giveBack(ticket);
      } catch (error) {
        logError(`Could not give back a ${ticket.kind} slot: ${redactError(error)}`);
      }
    },

    /**
     * Today's use and limits, and when they reset.
     * @param {string} userId
     */
    async report(userId) {
      const nowMilliseconds = now();
      const day = pacificDay(nowMilliseconds);
      const used = await usage.read(userId, day);
      const counts = {};
      for (const kind of Object.values(UsageKind)) {
        counts[kind] = { used: used[kind], limit: dailyLimit[kind] };
      }
      return { day, resetsAt: nextPacificMidnight(nowMilliseconds), counts };
    },
  };
}

/**
 * @param {string} kind
 * @param {number} limit
 */
function dailyLimitMessage(kind, limit) {
  return `You have used today's ${limit} ${KIND_LABELS[kind]}; the limit resets at midnight Pacific time`;
}

/** @param {string} hexadecimal */
function toObjectId(hexadecimal) {
  return ObjectId.createFromHexString(hexadecimal);
}

module.exports = {
  USAGE_COLLECTION,
  USAGE_LIFETIME_SECONDS,
  UsageKind,
  createDailyLimits,
  createMongoUsageStore,
  dailyLimitMessage,
};
