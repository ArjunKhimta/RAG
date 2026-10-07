/**
 * Daily limits reset at midnight Pacific time, when Gemini's free-tier daily quota resets, so a
 * person's allowance refills at the same moment as the quota it protects. Mirrors
 * `retrieval/src/retrieval/quota_clock.py`.
 *
 * Pacific midnight is 07:00 UTC in summer and 08:00 UTC in winter, so the day the clocks change is
 * 23 or 25 hours long. The next midnight is therefore found by asking the time zone database which
 * of the two candidate instants starts the next Pacific day, never by adding 24 hours.
 */

const PACIFIC_TIME_ZONE = "America/Los_Angeles";

const MIDNIGHT_UTC_HOURS = [7, 8];

const dayFormat = new Intl.DateTimeFormat("en-CA", {
  timeZone: PACIFIC_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/**
 * The Pacific date at an instant, as `YYYY-MM-DD`.
 * @param {number} milliseconds
 */
function pacificDay(milliseconds) {
  return dayFormat.format(new Date(milliseconds));
}

/**
 * The instant the next Pacific day begins.
 * @param {number} milliseconds
 * @returns {Date}
 */
function nextPacificMidnight(milliseconds) {
  const today = pacificDay(milliseconds);
  const [year, month, day] = today.split("-").map(Number);
  for (const hours of MIDNIGHT_UTC_HOURS) {
    const candidate = Date.UTC(year, month - 1, day + 1, hours);
    if (pacificDay(candidate) !== today && pacificDay(candidate - 1) === today) {
      return new Date(candidate);
    }
  }
  throw new Error(`No Pacific midnight found after ${today}`);
}

/**
 * Whole seconds until the next Pacific midnight, at least 1.
 * @param {number} milliseconds
 */
function secondsUntilPacificMidnight(milliseconds) {
  const remaining = nextPacificMidnight(milliseconds).getTime() - milliseconds;
  return Math.max(1, Math.ceil(remaining / 1000));
}

module.exports = { nextPacificMidnight, pacificDay, secondsUntilPacificMidnight };
