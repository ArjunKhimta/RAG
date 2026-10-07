const crypto = require("node:crypto");

const { RetrievalServiceError, UNKNOWN_JOB_MESSAGE } = require("../../src/retrievalService");

/**
 * A stand-in for the retrieval service client that keeps indexed versions and jobs in memory, as
 * the service does: an indexed version is `already_indexed`; the same version while its job runs
 * gets that job's ID; otherwise a new job starts. Tests move jobs along with `updateJob` and
 * `finishJob`, and make the next call fail with `failNextStart`, `failNextJob`, `failNextAsk`, or
 * `failNextSearch`. Questions and searches get a fixed reply naming what was asked.
 */
function createFakeRetrievalService() {
  const indexedVersions = new Set();
  const jobs = new Map();
  let nextStartError = null;
  let nextJobError = null;
  let nextAskError = null;
  let nextSearchError = null;

  function key(repository, version) {
    return `${repository}@${version}`;
  }

  return {
    startCalls: [],
    jobCalls: [],
    askCalls: [],
    searchCalls: [],

    failNextAsk(error) {
      nextAskError = error;
    },

    failNextSearch(error) {
      nextSearchError = error;
    },

    async ask(question) {
      this.askCalls.push(question);
      if (nextAskError !== null) {
        const error = nextAskError;
        nextAskError = null;
        throw error;
      }
      return askReply(question);
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
      if (indexedVersions.has(key(repository, version))) {
        return { status: "already_indexed", repository: repositoryBlock(repository, version) };
      }
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
      return { status: "running", jobId };
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
}

function repositoryBlock(repository, version) {
  const commitId = "c".repeat(40);
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

function askReply({ repository, version, question }) {
  return {
    found_answer: true,
    answer: `An answer to: ${question} [1, 303-305].`,
    sentences: [
      { text: `An answer to: ${question}`, citations: [{ source: 1, start_line: 303, end_line: 305 }] },
    ],
    sources: [{ number: 1, file_path: "src/flask/sessions.py", start_line: 303, end_line: 321 }],
    snippets: [],
    repository: repositoryBlock(repository, version),
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

module.exports = { createFakeRetrievalService, repositoryBlock };
