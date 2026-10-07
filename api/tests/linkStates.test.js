const {
  indexingState,
  readyState,
  resumeDecision,
  stateFromJob,
  unknownJobState,
} = require("../src/linkStates");
const { LinkStatus } = require("../src/repositoryLinks");
const { FLASK_BLOCK } = require("./support/repositoryLinkStoreContract");

const JOB_ID = "0123456789abcdef0123456789abcdef";

const NOW = Date.parse("2026-10-07T12:00:00Z");

function job(overrides) {
  return {
    job_id: JOB_ID,
    status: "running",
    stage: "embedding",
    progress: { embedded: 80, to_embed: 158 },
    error: null,
    resume_after: null,
    ...overrides,
  };
}

describe("states from the service's answers", () => {
  test("an indexed version is ready with its commit and license only", () => {
    expect(readyState({ name: "pallets/flask", version: "3.1.3", ...FLASK_BLOCK })).toEqual({
      status: LinkStatus.READY,
      jobId: null,
      stage: null,
      progress: null,
      error: null,
      resumeAfter: null,
      indexed: FLASK_BLOCK,
    });
  });

  test("a running job is indexing with its stage and progress", () => {
    expect(stateFromJob(job({}))).toEqual(
      indexingState(JOB_ID, { stage: "embedding", progress: { embedded: 80, to_embed: 158 } }),
    );
  });

  test("a job waiting for quota keeps its progress, error, and reset time, and no stage", () => {
    const state = stateFromJob(
      job({
        status: "waiting_for_quota",
        error: { code: "waiting_for_quota", message: "The daily quota is used up" },
        resume_after: "2026-10-08T07:00:00Z",
      }),
    );

    expect(state).toEqual({
      status: LinkStatus.WAITING_FOR_QUOTA,
      jobId: JOB_ID,
      stage: null,
      progress: { embedded: 80, to_embed: 158 },
      error: { code: "waiting_for_quota", message: "The daily quota is used up" },
      resumeAfter: new Date("2026-10-08T07:00:00Z"),
      indexed: null,
    });
  });

  test.each(["failed", "interrupted"])("a %s job keeps its error and no stage", (status) => {
    const error = { code: "not_found", message: "No such repository on GitHub" };

    expect(stateFromJob(job({ status, error }))).toMatchObject({
      status,
      jobId: JOB_ID,
      stage: null,
      progress: null,
      error,
    });
  });

  test("a status the service never sends gives no state", () => {
    expect(stateFromJob(job({ status: "exploded" }))).toBeNull();
  });

  test("a job the service no longer knows is interrupted", () => {
    expect(unknownJobState(JOB_ID)).toMatchObject({ status: LinkStatus.INTERRUPTED, jobId: JOB_ID });
  });
});

describe("whether indexing may start again", () => {
  function state(status, overrides = {}) {
    return { ...indexingState(JOB_ID), status, ...overrides };
  }

  test.each([LinkStatus.READY, LinkStatus.INDEXING])("a %s link has nothing to resume", (status) => {
    expect(resumeDecision(state(status), NOW)).toEqual({ allowed: false, reason: "not_needed" });
  });

  test.each(["clone_failed", "unexpected"])("a failure %p may be tried again", (code) => {
    const failed = state(LinkStatus.FAILED, { error: { code, message: "m" } });

    expect(resumeDecision(failed, NOW)).toEqual({ allowed: true });
  });

  test.each(["not_found", "not_public", "license_not_allowed", "too_large", "too_many_chunks"])(
    "a failure %p will recur and may not",
    (code) => {
      const failed = state(LinkStatus.FAILED, { error: { code, message: "m" } });

      expect(resumeDecision(failed, NOW)).toEqual({ allowed: false, reason: "permanent" });
    },
  );

  test("an interrupted link may resume", () => {
    expect(resumeDecision(state(LinkStatus.INTERRUPTED), NOW)).toEqual({ allowed: true });
  });

  test("waiting for quota may resume at the reset and not a moment before", () => {
    const resetAt = NOW + 90_500;
    const waiting = state(LinkStatus.WAITING_FOR_QUOTA, { resumeAfter: new Date(resetAt) });

    expect(resumeDecision(waiting, NOW)).toEqual({
      allowed: false,
      reason: "too_early",
      retryAfterSeconds: 91,
    });
    expect(resumeDecision(waiting, resetAt)).toEqual({ allowed: true });
  });
});
