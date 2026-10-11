/**
 * The one place the API calls the retrieval service (the Flask app in `retrieval/`).
 *
 * Every call sends the shared token as `Authorization: Bearer <token>` and has its own time limit:
 * a search takes tens of milliseconds, an overview one database read of the stored call graph,
 * starting or checking an indexing job one database round trip, and a question can wait up to a minute for Gemini's per-minute limit before its answer
 * takes a few seconds, so its limit is the longest.
 *
 * A successful reply is copied field by field into a new object, so a field the service adds later
 * (or a debugging detail) never reaches the browser by accident. A failure becomes a
 * `RetrievalServiceError` carrying the status and message the API should reply with:
 * - 400, 503: the service's own message, which names the problem without echoing values and reads
 *   well to a user (503 keeps the service's `Retry-After`)
 * - 409 (this version cannot be searched): a fixed message, since the service's is written for a
 *   developer at a terminal (it names the indexing script); the service's message is logged
 * - 502 from the service (the answer failed the citation check): a fixed message
 * - 404 for an unknown indexing job: a fixed message; a malformed job ID is refused without a call
 * - a timeout or a connection failure: 503 with `Retry-After: 30`
 * - anything else (the service refusing our token, a status it never sends, a reply that is not
 *   JSON): 502, logged with the status and path only, never the reply body
 */

const { ApiError } = require("./apiError");
const { logError } = require("./logging");
const { redact, redactError } = require("./redaction");

const TIMEOUT_MILLISECONDS = {
  search: 15_000,
  overview: 15_000,
  index: 30_000,
  ask: 90_000,
};

const UNAVAILABLE_RETRY_SECONDS = 30;

const BAD_REQUEST_STATUS = 400;

const NOT_FOUND_STATUS = 404;

const CONFLICT_STATUS = 409;

const BAD_GATEWAY_STATUS = 502;

const UNAVAILABLE_STATUS = 503;

const PASSED_ON_STATUSES = new Set([BAD_REQUEST_STATUS, UNAVAILABLE_STATUS]);

const INDEX_JOBS_PATH = "/index-jobs/";

const JOB_ID_PATTERN = /^[0-9a-f]{32}$/;

const RETRY_AFTER_PATTERN = /^\d+$/;

const UNAVAILABLE_MESSAGE = "The retrieval service is unavailable for now; try again shortly";

const UNEXPECTED_REPLY_MESSAGE = "The retrieval service gave an unexpected reply";

const REJECTED_ANSWER_MESSAGE = "The answer failed its citation check and was not shown";

const UNKNOWN_JOB_MESSAGE = "No such indexing job";

const NOT_SEARCHABLE_MESSAGE =
  "This repository version cannot be searched yet; it may still be indexing or need indexing again";

class RetrievalServiceError extends ApiError {
  /**
   * @param {number} status the status the API should reply with
   * @param {string} message safe to show to the user
   * @param {number | null} [retryAfterSeconds]
   */
  constructor(status, message, retryAfterSeconds = null) {
    super(status, message, retryAfterSeconds);
    this.name = "RetrievalServiceError";
  }
}

const REFUSED_BEFORE_GEMINI_STATUSES = new Set([
  BAD_REQUEST_STATUS,
  CONFLICT_STATUS,
  UNAVAILABLE_STATUS,
]);

/**
 * Whether the service refused before Gemini did any work (a bad request, a version that cannot be
 * searched, or the service, the database, or Gemini unavailable, including a time limit or a
 * connection failure), so a person's slot should be given back. An answer that failed its
 * citation check (502) used Gemini, so it counts.
 * @param {unknown} error
 */
function refusedBeforeGemini(error) {
  return error instanceof RetrievalServiceError && REFUSED_BEFORE_GEMINI_STATUSES.has(error.status);
}

class UnexpectedReplyError extends Error {
  constructor(description) {
    super(description);
    this.name = "UnexpectedReplyError";
  }
}

/**
 * @param {{
 *   baseUrl: string,
 *   token: string,
 *   fetchFunction?: typeof fetch,
 *   timeouts?: typeof TIMEOUT_MILLISECONDS,
 * }} options
 */
function createRetrievalService({
  baseUrl,
  token,
  fetchFunction = globalThis.fetch,
  timeouts = TIMEOUT_MILLISECONDS,
}) {
  const root = baseUrl.replace(/\/+$/, "");

  async function call(method, path, body, timeoutMilliseconds) {
    try {
      const response = await fetchFunction(`${root}${path}`, {
        method,
        headers: requestHeaders(token, body),
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMilliseconds),
        redirect: "error",
      });
      return { status: response.status, headers: response.headers, body: await readJson(response) };
    } catch (error) {
      logError(`Retrieval service ${method} ${path} failed: ${redactError(error)}`);
      throw unavailable();
    }
  }

  async function request(method, path, body, timeoutMilliseconds, readSuccess) {
    const reply = await call(method, path, body, timeoutMilliseconds);
    try {
      if (reply.status === 200 || reply.status === 202) {
        return readSuccess(reply);
      }
      throw failure(reply, path);
    } catch (error) {
      if (error instanceof UnexpectedReplyError) {
        logError(
          `Retrieval service ${method} ${path} gave an unexpected reply ` +
            `(status ${reply.status}): ${error.message}`,
        );
        throw new RetrievalServiceError(BAD_GATEWAY_STATUS, UNEXPECTED_REPLY_MESSAGE);
      }
      throw error;
    }
  }

  return {
    /**
     * @param {{
     *   repository: string, version: string, question: string, excludeTests?: boolean,
     * }} question
     */
    ask({ repository, version, question, excludeTests }) {
      const body = withExcludeTests({ repository, version, question }, excludeTests);
      return request("POST", "/ask", body, timeouts.ask, (reply) => askReply(reply.body));
    },

    /**
     * @param {{
     *   repository: string, version: string, query: string, limit?: number,
     *   excludeTests?: boolean,
     * }} search
     */
    search({ repository, version, query, limit, excludeTests }) {
      const body = withExcludeTests({ repository, version, query }, excludeTests);
      if (limit !== undefined) {
        body.limit = limit;
      }
      return request("POST", "/search", body, timeouts.search, (reply) => searchReply(reply.body));
    },

    /** @param {{ repository: string, version: string }} target */
    overview({ repository, version }) {
      return request("POST", "/overview", { repository, version }, timeouts.overview, (reply) =>
        overviewReply(reply.body),
      );
    },

    /** @param {{ repository: string, version: string }} target */
    startIndexing({ repository, version }) {
      return request("POST", "/index", { repository, version }, timeouts.index, startReply);
    },

    /** @param {string} jobId */
    async indexJob(jobId) {
      if (typeof jobId !== "string" || !JOB_ID_PATTERN.test(jobId)) {
        throw new RetrievalServiceError(NOT_FOUND_STATUS, UNKNOWN_JOB_MESSAGE);
      }
      return request("GET", `${INDEX_JOBS_PATH}${jobId}`, undefined, timeouts.index, (reply) =>
        jobReply(reply.body),
      );
    },
  };
}

function requestHeaders(token, body) {
  const headers = { Authorization: `Bearer ${token}`, Accept: "application/json" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
}

function withExcludeTests(body, excludeTests) {
  if (excludeTests !== undefined) {
    body.exclude_tests = excludeTests;
  }
  return body;
}

async function readJson(response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function unavailable() {
  return new RetrievalServiceError(
    UNAVAILABLE_STATUS,
    UNAVAILABLE_MESSAGE,
    UNAVAILABLE_RETRY_SECONDS,
  );
}

function failure(reply, path) {
  if (reply.status === BAD_GATEWAY_STATUS) {
    return new RetrievalServiceError(BAD_GATEWAY_STATUS, REJECTED_ANSWER_MESSAGE);
  }
  if (reply.status === NOT_FOUND_STATUS && path.startsWith(INDEX_JOBS_PATH)) {
    return new RetrievalServiceError(NOT_FOUND_STATUS, UNKNOWN_JOB_MESSAGE);
  }
  if (reply.status === CONFLICT_STATUS) {
    logError(`Retrieval service refused ${path}: ${redact(String(errorMessage(reply)))}`);
    return new RetrievalServiceError(CONFLICT_STATUS, NOT_SEARCHABLE_MESSAGE);
  }
  if (!PASSED_ON_STATUSES.has(reply.status)) {
    return new UnexpectedReplyError("a status the service does not send");
  }
  const message = errorMessage(reply);
  if (typeof message !== "string" || message.length === 0) {
    return new UnexpectedReplyError("an error reply without a message");
  }
  const retryAfterSeconds =
    reply.status === UNAVAILABLE_STATUS ? readRetryAfter(reply.headers.get("Retry-After")) : null;
  return new RetrievalServiceError(reply.status, message, retryAfterSeconds);
}

function errorMessage(reply) {
  return isObject(reply.body) ? reply.body.error : undefined;
}

function readRetryAfter(headerValue) {
  if (headerValue !== null && RETRY_AFTER_PATTERN.test(headerValue)) {
    return Number(headerValue);
  }
  return UNAVAILABLE_RETRY_SECONDS;
}

function startReply(reply) {
  const body = requireObject(reply.body, "start reply");
  if (reply.status === 200 && body.status === "already_indexed") {
    return { status: "already_indexed", repository: repositoryBlock(body.repository) };
  }
  if (reply.status === 202 && body.status === "running" && JOB_ID_PATTERN.test(body.job_id)) {
    return { status: "running", jobId: body.job_id };
  }
  throw new UnexpectedReplyError("a start reply with an unknown status or job ID");
}

function askReply(body) {
  const reply = requireObject(body, "ask reply");
  return {
    found_answer: reply.found_answer,
    answer: reply.answer,
    sentences: listOf(reply.sentences, sentence),
    sources: listOf(reply.sources, answerSource),
    snippets: listOf(reply.snippets, snippet),
    repository: repositoryBlock(reply.repository),
    usage: reply.usage === null ? null : pick(reply.usage, ASK_USAGE_FIELDS),
    timings_ms: timings(reply.timings_ms),
  };
}

function searchReply(body) {
  const reply = requireObject(body, "search reply");
  return {
    results: listOf(reply.results, searchResult),
    repository: repositoryBlock(reply.repository),
    usage: pick(reply.usage, ["embedding"]),
    timings_ms: timings(reply.timings_ms),
  };
}

/**
 * A repository version's files and the calls between them, from the stored call graph.
 * @param {unknown} body
 */
function overviewReply(body) {
  const reply = requireObject(body, "overview reply");
  return {
    repository: repositoryBlock(reply.repository),
    files: listOf(reply.files, (file) => pick(file, OVERVIEW_FILE_FIELDS)),
    links: listOf(reply.links, (link) => pick(link, ["from", "to", "calls"])),
    totals: pick(reply.totals, OVERVIEW_TOTAL_FIELDS),
    left_out: leftOut(reply.left_out),
    timings_ms: timings(reply.timings_ms),
  };
}

/**
 * What the overview left out: folder names as text, and how many files and calls.
 * @param {unknown} value
 */
function leftOut(value) {
  const fields = requireObject(value, "left out");
  return {
    folders: listOf(fields.folders, (folder) => {
      if (typeof folder !== "string") {
        throw new UnexpectedReplyError("a left-out folder that is not text");
      }
      return folder;
    }),
    files: fields.files,
    calls: fields.calls,
  };
}

function jobReply(body) {
  const reply = requireObject(body, "job reply");
  return {
    ...pick(reply, [
      "job_id",
      "repository",
      "version",
      "status",
      "stage",
      "resume_after",
      "retry_after_seconds",
      "created_at",
      "finished_at",
    ]),
    progress: pick(reply.progress, ["embedded", "to_embed"]),
    error: reply.error === null ? null : pick(reply.error, ["code", "message"]),
    result: reply.result === null ? null : pick(reply.result, JOB_RESULT_FIELDS),
    timings_ms: reply.timings_ms === null ? null : timings(reply.timings_ms),
  };
}

const OVERVIEW_FILE_FIELDS = [
  "path",
  "classes",
  "functions",
  "calls_within",
  "calls_out",
  "calls_in",
  "link",
];

const OVERVIEW_TOTAL_FIELDS = [
  "files",
  "classes",
  "functions",
  "calls",
  "calls_between_files",
  "calls_within_files",
];

const ASK_USAGE_FIELDS = ["embedding", "answer_requests", "prompt_tokens", "output_tokens"];

const ANSWER_SOURCE_FIELDS = [
  "number",
  "file_path",
  "kind",
  "qualified_name",
  "start_line",
  "end_line",
  "link",
];

const JOB_RESULT_FIELDS = [
  "commit_id",
  "license_spdx_id",
  "indexed_file_count",
  "secret_finding_count",
  "call_graph_edge_count",
  "chunk_count",
  "cache_hit_count",
  "embedded_input_count",
  "embedding_request_count",
  "retry_count",
  "truncated_chunk_count",
  "deleted_stale_chunk_count",
  "rate_limit_wait_seconds",
];

function repositoryBlock(value) {
  const block = pick(value, ["name", "version", "commit_id"]);
  block.license = pick(value.license, ["spdx_id", "name", "path", "link"]);
  return block;
}

function sentence(value) {
  const fields = requireObject(value, "sentence");
  return { text: fields.text, citations: listOf(fields.citations, citation) };
}

function citation(value) {
  return pick(value, ["source", "start_line", "end_line"]);
}

function answerSource(value) {
  return pick(value, ANSWER_SOURCE_FIELDS);
}

function snippet(value) {
  return {
    ...pick(value, ["source", "start_line", "end_line", "link"]),
    ...snippetLines(value),
  };
}

function searchResult(value) {
  return {
    ...pick(value, [
      "rank",
      "file_path",
      "kind",
      "qualified_name",
      "start_line",
      "end_line",
      "is_test_file",
      "score",
      "link",
    ]),
    snippet: snippetLines(requireObject(value, "search result").snippet),
  };
}

function snippetLines(value) {
  return {
    ...pick(value, ["is_outline", "more_lines"]),
    lines: listOf(value.lines, (line) => pick(line, ["number", "text"])),
  };
}

function timings(value) {
  const stages = requireObject(value, "timings");
  const copied = {};
  for (const [stage, milliseconds] of Object.entries(stages)) {
    if (typeof milliseconds === "number") {
      copied[stage] = milliseconds;
    }
  }
  return copied;
}

function pick(value, fieldNames) {
  const source = requireObject(value, fieldNames.join(", "));
  const copied = {};
  for (const fieldName of fieldNames) {
    if (fieldName in source) {
      copied[fieldName] = source[fieldName];
    }
  }
  return copied;
}

function listOf(value, readItem) {
  if (!Array.isArray(value)) {
    throw new UnexpectedReplyError("a list field that is not a list");
  }
  return value.map(readItem);
}

function requireObject(value, description) {
  if (!isObject(value)) {
    throw new UnexpectedReplyError(`${description} is not an object`);
  }
  return value;
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

module.exports = {
  NOT_SEARCHABLE_MESSAGE,
  REJECTED_ANSWER_MESSAGE,
  TIMEOUT_MILLISECONDS,
  UNAVAILABLE_MESSAGE,
  UNAVAILABLE_RETRY_SECONDS,
  UNEXPECTED_REPLY_MESSAGE,
  UNKNOWN_JOB_MESSAGE,
  RetrievalServiceError,
  createRetrievalService,
  refusedBeforeGemini,
};
