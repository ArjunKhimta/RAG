/**
 * Every per-person limit in one place. The daily ones share Gemini's free tier fairly between
 * people (questions use the answer model and an embedding, searches an embedding, indexing many
 * embeddings); the totals keep each person's stored data bounded; sign-in attempts are limited
 * per internet address. Starting values, to be revisited with real use.
 */

const DEFAULT_LIMITS = Object.freeze({
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

/**
 * @param {Partial<typeof DEFAULT_LIMITS>} [overrides]
 * @returns {typeof DEFAULT_LIMITS}
 */
function withLimits(overrides = {}) {
  return Object.freeze({ ...DEFAULT_LIMITS, ...overrides });
}

module.exports = { DEFAULT_LIMITS, withLimits };
