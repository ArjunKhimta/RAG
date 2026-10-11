/**
 * How a repository link's state follows from what the retrieval service says, and whether
 * indexing may be started again.
 *
 * - The service says a version is already indexed: the link is `ready`, with the indexed commit
 *   and license (and its link to the LICENSE file), which every answer must show.
 * - A job is running: `indexing`, with its stage and progress.
 * - A job finished in any other way: its status as the service names it (`waiting_for_quota`,
 *   `failed`, `interrupted`), with its error and, when waiting, when the quota resets.
 *
 * A job that succeeded is not turned into `ready` here: its record lacks the license link, so the
 * routes ask the service for the version once more (an `already_indexed` reply).
 *
 * Indexing may start again (resume) after the quota resets, after an interruption, or after a
 * failure that may pass (`clone_failed`, `unexpected`). A failure that will recur (the repository
 * does not exist, is private, has a license not allowed, or is too large) cannot be resumed; the
 * person removes the link instead.
 */

const { LinkStatus } = require("./repositoryLinks");

const JobStatus = Object.freeze({
  RUNNING: "running",
  SUCCEEDED: "succeeded",
  WAITING_FOR_QUOTA: "waiting_for_quota",
  FAILED: "failed",
  INTERRUPTED: "interrupted",
});

const RETRYABLE_ERROR_CODES = new Set(["clone_failed", "unexpected"]);

const EMPTY_STATE = Object.freeze({
  jobId: null,
  stage: null,
  progress: null,
  error: null,
  resumeAfter: null,
  indexed: null,
});

/**
 * @param {{ commit_id: string, license: Record<string, string>, other_language_files?: Array<{ language: string, files: number }> | null }} repositoryBlock
 * @returns {import("./repositoryLinks").LinkState}
 */
function readyState(repositoryBlock) {
  return {
    ...EMPTY_STATE,
    status: LinkStatus.READY,
    indexed: {
      commit_id: repositoryBlock.commit_id,
      license: repositoryBlock.license,
      other_language_files: repositoryBlock.other_language_files ?? null,
    },
  };
}

/**
 * @param {string} jobId
 * @param {{ stage?: string | null, progress?: { embedded: number | null, to_embed: number | null } | null }} [details]
 * @returns {import("./repositoryLinks").LinkState}
 */
function indexingState(jobId, { stage = null, progress = null } = {}) {
  return { ...EMPTY_STATE, status: LinkStatus.INDEXING, jobId, stage, progress };
}

/**
 * The link's state for a job the service no longer knows (its record expires after 30 days).
 * @param {string | null} jobId
 */
function unknownJobState(jobId) {
  return { ...EMPTY_STATE, status: LinkStatus.INTERRUPTED, jobId };
}

/**
 * The link's state for a job that has not succeeded; null for a status the service never sends.
 * @param {{
 *   job_id: string,
 *   status: string,
 *   stage: string | null,
 *   progress: { embedded: number | null, to_embed: number | null },
 *   error: { code: string, message: string } | null,
 *   resume_after: string | null,
 * }} job
 * @returns {import("./repositoryLinks").LinkState | null}
 */
function stateFromJob(job) {
  if (job.status === JobStatus.RUNNING) {
    return indexingState(job.job_id, { stage: job.stage, progress: job.progress });
  }
  if (job.status === JobStatus.WAITING_FOR_QUOTA) {
    return {
      ...EMPTY_STATE,
      status: LinkStatus.WAITING_FOR_QUOTA,
      jobId: job.job_id,
      progress: job.progress,
      error: job.error,
      resumeAfter: job.resume_after === null ? null : new Date(job.resume_after),
    };
  }
  if (job.status === JobStatus.FAILED || job.status === JobStatus.INTERRUPTED) {
    return { ...EMPTY_STATE, status: job.status, jobId: job.job_id, error: job.error };
  }
  return null;
}

/**
 * Whether indexing may be started again for a link in this state, at this time.
 * @param {import("./repositoryLinks").LinkState} state
 * @param {number} nowMilliseconds
 * @returns {{ allowed: true } | { allowed: false, reason: "not_needed" | "permanent" | "too_early", retryAfterSeconds?: number }}
 */
function resumeDecision(state, nowMilliseconds) {
  if (state.status === LinkStatus.READY || state.status === LinkStatus.INDEXING) {
    return { allowed: false, reason: "not_needed" };
  }
  if (state.status === LinkStatus.FAILED) {
    const code = state.error === null ? null : state.error.code;
    return RETRYABLE_ERROR_CODES.has(code)
      ? { allowed: true }
      : { allowed: false, reason: "permanent" };
  }
  if (state.status === LinkStatus.WAITING_FOR_QUOTA && state.resumeAfter !== null) {
    const waitMilliseconds = state.resumeAfter.getTime() - nowMilliseconds;
    if (waitMilliseconds > 0) {
      return {
        allowed: false,
        reason: "too_early",
        retryAfterSeconds: Math.ceil(waitMilliseconds / 1000),
      };
    }
  }
  return { allowed: true };
}

module.exports = {
  JobStatus,
  RETRYABLE_ERROR_CODES,
  indexingState,
  readyState,
  resumeDecision,
  stateFromJob,
  unknownJobState,
};
