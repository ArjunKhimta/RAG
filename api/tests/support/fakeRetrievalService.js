const crypto = require("node:crypto");

const { RetrievalServiceError, UNKNOWN_JOB_MESSAGE } = require("../../src/retrievalService");

/**
 * A stand-in for the retrieval service client that keeps indexed versions and jobs in memory, as
 * the service does: an indexed version is `already_indexed`; the same version while its job runs
 * gets that job's ID; otherwise a new job starts. Tests move jobs along with `updateJob` and
 * `finishJob`, and make the next call fail with `failNextStart`, `failNextJob`, `failNextAsk`,
 * `failNextSearch`, `failNextOverview`, or `failNextRefresh`. Questions, searches, and overviews
 * get a fixed reply naming what was asked.
 *
 * Every indexed version is at commit `c` repeated until `moveVersion` points its tag or branch
 * somewhere else; a refresh of a moved version starts a job, and finishing that job makes the new
 * commit the indexed one. `setIndexedCommit` changes the indexed commit at once, as a refresh by
 * someone else would. Answers carry the version's indexed commit.
 */
function createFakeRetrievalService() {
  const indexedVersions = new Set();
  const indexedCommits = new Map();
  const remoteCommits = new Map();
  const jobs = new Map();
  const targetCommitsByJob = new Map();
  let nextStartError = null;
  let nextRefreshError = null;
  let nextJobError = null;
  let nextAskError = null;
  let nextSearchError = null;
  let nextOverviewError = null;

  function key(repository, version) {
    return `${repository}@${version}`;
  }

  return {
    startCalls: [],
    refreshCalls: [],
    jobCalls: [],
    askCalls: [],
    searchCalls: [],
    overviewCalls: [],

    failNextAsk(error) {
      nextAskError = error;
    },

    failNextSearch(error) {
      nextSearchError = error;
    },

    failNextOverview(error) {
      nextOverviewError = error;
    },

    async overview(target) {
      this.overviewCalls.push(target);
      if (nextOverviewError !== null) {
        const error = nextOverviewError;
        nextOverviewError = null;
        throw error;
      }
      return overviewReply(target);
    },

    async ask(question) {
      this.askCalls.push(question);
      if (nextAskError !== null) {
        const error = nextAskError;
        nextAskError = null;
        throw error;
      }
      return askReply(question, indexedCommitOf(question.repository, question.version));
    },

    async search(search) {
      this.searchCalls.push(search);
      if (nextSearchError !== null) {
        const error = nextSearchError;
        nextSearchError = null;
        throw error;
      }
      return searchReply(search);
    },

    markIndexed(repository, version) {
      indexedVersions.add(key(repository, version));
    },

    jobFor(repository, version) {
      return [...jobs.values()].find(
        (job) => job.repository === repository && job.version === version,
      );
    },

    updateJob(jobId, changes) {
      Object.assign(jobs.get(jobId), changes);
    },

    finishJob(jobId) {
      const job = jobs.get(jobId);
      job.status = "succeeded";
      indexedVersions.add(key(job.repository, job.version));
      if (targetCommitsByJob.has(jobId)) {
        indexedCommits.set(key(job.repository, job.version), targetCommitsByJob.get(jobId));
      }
    },

    setIndexedCommit(repository, version, commitId) {
      indexedVersions.add(key(repository, version));
      indexedCommits.set(key(repository, version), commitId);
    },

    moveVersion(repository, version, commitId) {
      remoteCommits.set(key(repository, version), commitId);
    },

    failNextRefresh(error) {
      nextRefreshError = error;
    },

    async refreshIndexing({ repository, version }) {
      this.refreshCalls.push({ repository, version });
      if (nextRefreshError !== null) {
        const error = nextRefreshError;
        nextRefreshError = null;
        throw error;
      }
      const versionKey = key(repository, version);
      if (!indexedVersions.has(versionKey)) {
        return startJob(repository, version);
      }
      const indexedCommit = indexedCommits.get(versionKey) ?? DEFAULT_COMMIT;
      const remoteCommit = remoteCommits.get(versionKey) ?? indexedCommit;
      if (remoteCommit === indexedCommit) {
        return {
          status: "up_to_date",
          repository: repositoryBlock(repository, version, indexedCommit),
        };
      }
      return startJob(repository, version, remoteCommit);
    },

    forgetJob(jobId) {
      jobs.delete(jobId);
    },

    failNextStart(error) {
      nextStartError = error;
    },

    failNextJob(error) {
      nextJobError = error;
    },

    async startIndexing({ repository, version }) {
      this.startCalls.push({ repository, version });
      if (nextStartError !== null) {
        const error = nextStartError;
        nextStartError = null;
        throw error;
      }
      const versionKey = key(repository, version);
      if (indexedVersions.has(versionKey)) {
        const commitId = indexedCommits.get(versionKey) ?? DEFAULT_COMMIT;
        return {
          status: "already_indexed",
          repository: repositoryBlock(repository, version, commitId),
        };
      }
      return startJob(repository, version);
    },

    async indexJob(jobId) {
      this.jobCalls.push(jobId);
      if (nextJobError !== null) {
        const error = nextJobError;
        nextJobError = null;
        throw error;
      }
      const job = jobs.get(jobId);
      if (job === undefined) {
        throw new RetrievalServiceError(404, UNKNOWN_JOB_MESSAGE);
      }
      return { ...job, progress: { ...job.progress } };
    },
  };

  function indexedCommitOf(repository, version) {
    return indexedCommits.get(key(repository, version)) ?? DEFAULT_COMMIT;
  }

  function startJob(repository, version, targetCommit) {
    const running = [...jobs.values()].find(
      (job) =>
        job.repository === repository && job.version === version && job.status === "running",
    );
    if (running !== undefined) {
      return { status: "running", jobId: running.job_id };
    }
    const jobId = crypto.randomBytes(16).toString("hex");
    jobs.set(jobId, {
      job_id: jobId,
      repository,
      version,
      status: "running",
      stage: "cloning",
      progress: { embedded: 0, to_embed: null },
      error: null,
      resume_after: null,
    });
    if (targetCommit !== undefined) {
      targetCommitsByJob.set(jobId, targetCommit);
    }
    return { status: "running", jobId };
  }
}

const DEFAULT_COMMIT = "c".repeat(40);

function repositoryBlock(repository, version, commitId = DEFAULT_COMMIT) {
  return {
    name: repository,
    version,
    commit_id: commitId,
    license: {
      spdx_id: "BSD-3-Clause",
      name: "BSD 3-Clause",
      path: "LICENSE.txt",
      link: `https://github.com/${repository}/blob/${commitId}/LICENSE.txt`,
    },
  };
}

function askReply({ repository, version, question }, commitId) {
  return {
    found_answer: true,
    answer: `An answer to: ${question} [1, 303-305].`,
    sentences: [
      { text: `An answer to: ${question}`, citations: [{ source: 1, start_line: 303, end_line: 305 }] },
    ],
    sources: [{ number: 1, file_path: "src/flask/sessions.py", start_line: 303, end_line: 321 }],
    snippets: [],
    repository: repositoryBlock(repository, version, commitId),
    usage: { embedding: "from the cache", answer_requests: 1, prompt_tokens: 2000, output_tokens: 100 },
    timings_ms: { checks: 30, "generate answer": 1500 },
  };
}

function searchReply({ repository, version, query }) {
  return {
    results: [{ rank: 1, file_path: "src/flask/sessions.py", qualified_name: query }],
    repository: repositoryBlock(repository, version),
    usage: { embedding: "from the cache" },
    timings_ms: { "vector search": 45 },
  };
}

function overviewReply({ repository, version }) {
  return {
    repository: repositoryBlock(repository, version),
    files: [{ path: "src/flask/app.py", classes: 1, functions: 40, calls_within: 12, calls_out: 9, calls_in: 2 }],
    links: [{ from: "src/flask/app.py", to: "src/flask/helpers.py", calls: 9 }],
    totals: { files: 1, classes: 1, functions: 40, calls: 21, calls_between_files: 9, calls_within_files: 12 },
    left_out: { folders: ["examples/"], files: 10, calls: 41 },
    timings_ms: { checks: 31, "summarize call graph": 19 },
  };
}

module.exports = { createFakeRetrievalService, repositoryBlock };
