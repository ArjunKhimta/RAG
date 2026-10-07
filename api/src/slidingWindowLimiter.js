/**
 * A short-window limit kept in the server's memory: at most `limit` uses by one key (a person, or
 * an internet address) in any `windowMilliseconds`, counted over a sliding window, so a burst
 * cannot get twice the limit by straddling a minute boundary.
 *
 * Each key keeps the times of its recent uses (never more than `limit`). Keys with no recent use
 * are dropped whenever the map grows past `PRUNE_THRESHOLD`, so memory stays bounded. The counts
 * are forgotten when the server restarts, which is harmless for limits measured in minutes; the
 * daily limits that protect the quota live in the database.
 */

const PRUNE_THRESHOLD = 1000;

/**
 * @param {{ limit: number, windowMilliseconds: number, now?: () => number }} options
 */
function createSlidingWindowLimiter({ limit, windowMilliseconds, now = Date.now }) {
  const recentUses = new Map();

  function recentFor(key, nowMilliseconds) {
    const cutoff = nowMilliseconds - windowMilliseconds;
    const times = (recentUses.get(key) ?? []).filter((time) => time > cutoff);
    if (times.length === 0) {
      recentUses.delete(key);
    } else {
      recentUses.set(key, times);
    }
    return times;
  }

  function pruneIfLarge(nowMilliseconds) {
    if (recentUses.size <= PRUNE_THRESHOLD) {
      return;
    }
    for (const key of [...recentUses.keys()]) {
      recentFor(key, nowMilliseconds);
    }
  }

  return {
    /**
     * Use one slot for `key` if one is free.
     * @param {string} key
     * @returns {{ taken: true, time: number } | { taken: false, retryAfterSeconds: number }}
     */
    take(key) {
      const nowMilliseconds = now();
      pruneIfLarge(nowMilliseconds);
      const times = recentFor(key, nowMilliseconds);
      if (times.length >= limit) {
        const freedAt = times[0] + windowMilliseconds;
        return {
          taken: false,
          retryAfterSeconds: Math.max(1, Math.ceil((freedAt - nowMilliseconds) / 1000)),
        };
      }
      recentUses.set(key, [...times, nowMilliseconds]);
      return { taken: true, time: nowMilliseconds };
    },

    /**
     * Return a slot taken at `time`.
     * @param {string} key
     * @param {number} time
     */
    giveBack(key, time) {
      const times = recentUses.get(key);
      if (times === undefined) {
        return;
      }
      const index = times.lastIndexOf(time);
      if (index !== -1) {
        times.splice(index, 1);
      }
      if (times.length === 0) {
        recentUses.delete(key);
      }
    },

    /** How many keys are held; for tests of the pruning. */
    size() {
      return recentUses.size;
    },
  };
}

module.exports = { PRUNE_THRESHOLD, createSlidingWindowLimiter };
