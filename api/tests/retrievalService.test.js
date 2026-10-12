const http = require("node:http");

const {
  NOT_SEARCHABLE_MESSAGE,
  REJECTED_ANSWER_MESSAGE,
  STARTING_UP_MESSAGE,
  TIMEOUT_MILLISECONDS,
  UNAVAILABLE_MESSAGE,
  UNEXPECTED_REPLY_MESSAGE,
  UNKNOWN_JOB_MESSAGE,
  VERSION_GONE_MESSAGE,
  RetrievalServiceError,
  createRetrievalService,
  refusedBeforeGemini,
} = require("../src/retrievalService");

const TOKEN = "service-token-for-tests-0123456789abcdef";

const JOB_ID = "0123456789abcdef0123456789abcdef";

const REPOSITORY_BLOCK = {
  name: "pallets/flask",
  version: "3.1.3",
  commit_id: "22d924701a6ae2e4cd01e9a15bbaf3946094af65",
  license: {
    spdx_id: "BSD-3-Clause",
    name: "BSD 3-Clause",
    path: "LICENSE.txt",
    link: "https://github.com/pallets/flask/blob/22d9247/LICENSE.txt",
    internal_note: "dropped",
  },
  internal_note: "dropped",
};

const SNIPPET_LINES = {
  is_outline: false,
  lines: [{ number: 303, text: "    def get_signing_serializer(self, app):", extra: "dropped" }],
  more_lines: 0,
};

const ASK_REPLY = {
  found_answer: true,
  answer: "It signs the cookie [1, 303-305].",
  sentences: [
    { text: "It signs the cookie.", citations: [{ source: 1, start_line: 303, end_line: 305 }] },
  ],
  sources: [
    {
      number: 1,
      file_path: "src/flask/sessions.py",
      kind: "method",
      qualified_name: "SecureCookieSessionInterface.get_signing_serializer",
      start_line: 303,
      end_line: 321,
      link: "https://github.com/pallets/flask/blob/22d9247/src/flask/sessions.py#L303-L321",
      text: "dropped: sources never carry code",
    },
  ],
  snippets: [
    {
      source: 1,
      start_line: 303,
      end_line: 305,
      link: "https://github.com/pallets/flask/blob/22d9247/src/flask/sessions.py#L303-L305",
      ...SNIPPET_LINES,
    },
  ],
  repository: REPOSITORY_BLOCK,
  usage: {
    embedding: "from the cache",
    answer_requests: 1,
    prompt_tokens: 2213,
    output_tokens: 120,
  },
  timings_ms: { checks: 35, "embed query": 34, "vector search": 46, "generate answer": 1586 },
  debug: "dropped",
};

const SEARCH_REPLY = {
  results: [
    {
      rank: 1,
      file_path: "src/flask/sessions.py",
      kind: "method",
      qualified_name: "SecureCookieSessionInterface.get_signing_serializer",
      start_line: 303,
      end_line: 321,
      is_test_file: false,
      score: 0.81,
      link: "https://github.com/pallets/flask/blob/22d9247/src/flask/sessions.py#L303-L321",
      snippet: SNIPPET_LINES,
      embedding: "dropped",
    },
  ],
  repository: REPOSITORY_BLOCK,
  usage: { embedding: "embedded", extra: "dropped" },
  timings_ms: { checks: 31, "embed query": 706, "vector search": 50 },
};

const OVERVIEW_REPLY = {
  repository: REPOSITORY_BLOCK,
  files: [
    {
      path: "src/flask/app.py",
      classes: 1,
      functions: 40,
      calls_within: 12,
      calls_out: 9,
      calls_in: 2,
      link: "https://github.com/pallets/flask/blob/22d9247/src/flask/app.py",
      text: "dropped: overviews never carry code",
    },
  ],
  links: [{ from: "src/flask/app.py", to: "src/flask/helpers.py", calls: 9, weight: "dropped" }],
  totals: {
    files: 1,
    classes: 1,
    functions: 40,
    calls: 21,
    calls_between_files: 9,
    calls_within_files: 12,
    extra: "dropped",
  },
  left_out: { folders: ["examples/"], files: 10, calls: 41, extra: "dropped" },
  timings_ms: { checks: 31, "summarize call graph": 19 },
  debug: "dropped",
};

const JOB_REPLY = {
  job_id: JOB_ID,
  repository: "pallets/itsdangerous",
  version: "2.2.0",
  status: "succeeded",
  stage: "embedding",
  progress: { embedded: 158, to_embed: 158 },
  error: null,
  resume_after: null,
  retry_after_seconds: null,
  result: { commit_id: "096c8d4", chunk_count: 158, cache_hit_count: 0, extra: "dropped" },
  timings_ms: { "clone or reuse": 3400 },
  created_at: "2026-10-05T10:00:00Z",
  finished_at: "2026-10-05T10:01:10Z",
  extra: "dropped",
};

let service;
let errorLog;

beforeEach(async () => {
  errorLog = jest.spyOn(console, "error").mockImplementation(() => {});
  service = await startFakeService();
});

afterEach(async () => {
  errorLog.mockRestore();
  await service.close();
});

function client(options = {}) {
  return createRetrievalService({ baseUrl: service.url, token: TOKEN, ...options });
}

function loggedText() {
  return errorLog.mock.calls.map((call) => call.join(" ")).join("\n");
}

async function caught(promise) {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  throw new Error("expected the call to fail");
}

describe("successful replies", () => {
  test("a question is posted with the token; unknown reply fields are dropped", async () => {
    service.reply({ status: 200, body: ASK_REPLY });

    const reply = await client().ask({
      repository: "pallets/flask",
      version: "3.1.3",
      question: "How is the session cookie signed?",
      excludeTests: true,
    });

    const sent = service.requests[0];
    expect(sent.method).toBe("POST");
    expect(sent.path).toBe("/ask");
    expect(sent.headers.authorization).toBe(`Bearer ${TOKEN}`);
    expect(sent.headers["content-type"]).toBe("application/json");
    expect(JSON.parse(sent.body)).toEqual({
      repository: "pallets/flask",
      version: "3.1.3",
      question: "How is the session cookie signed?",
      exclude_tests: true,
    });
    expect(reply.debug).toBeUndefined();
    expect(reply.repository.internal_note).toBeUndefined();
    expect(reply.repository.license).toEqual({
      spdx_id: "BSD-3-Clause",
      name: "BSD 3-Clause",
      path: "LICENSE.txt",
      link: "https://github.com/pallets/flask/blob/22d9247/LICENSE.txt",
    });
    expect(reply.sources[0].text).toBeUndefined();
    expect(reply.sources[0].qualified_name).toBe(
      "SecureCookieSessionInterface.get_signing_serializer",
    );
    expect(reply.snippets[0].lines).toEqual([
      { number: 303, text: "    def get_signing_serializer(self, app):" },
    ]);
    expect(reply.sentences).toEqual(ASK_REPLY.sentences);
    expect(reply.usage).toEqual(ASK_REPLY.usage);
    expect(reply.timings_ms).toEqual(ASK_REPLY.timings_ms);
  });

  test("exclude_tests is left out when not given", async () => {
    service.reply({ status: 200, body: ASK_REPLY });

    await client().ask({ repository: "pallets/flask", version: "3.1.3", question: "Why?" });

    expect(JSON.parse(service.requests[0].body)).toEqual({
      repository: "pallets/flask",
      version: "3.1.3",
      question: "Why?",
    });
  });

  test("a no-match answer keeps its null usage", async () => {
    const noMatch = { ...ASK_REPLY, found_answer: false, sentences: [], sources: [], snippets: [] };
    service.reply({ status: 200, body: { ...noMatch, usage: null, timings_ms: {} } });

    const reply = await client().ask({ repository: "a/b", version: "1", question: "x" });

    expect(reply.found_answer).toBe(false);
    expect(reply.usage).toBeNull();
  });

  test("a search sends its limit and its results lose unknown fields", async () => {
    service.reply({ status: 200, body: SEARCH_REPLY });

    const reply = await client().search({
      repository: "pallets/flask",
      version: "3.1.3",
      query: "url_for",
      limit: 3,
    });

    expect(JSON.parse(service.requests[0].body)).toEqual({
      repository: "pallets/flask",
      version: "3.1.3",
      query: "url_for",
      limit: 3,
    });
    expect(reply.results[0].embedding).toBeUndefined();
    expect(reply.results[0].score).toBe(0.81);
    expect(reply.results[0].snippet.lines).toEqual([
      { number: 303, text: "    def get_signing_serializer(self, app):" },
    ]);
    expect(reply.usage).toEqual({ embedding: "embedded" });
  });

  test("an overview is posted for the version and loses unknown fields", async () => {
    service.reply({ status: 200, body: OVERVIEW_REPLY });

    const reply = await client().overview({ repository: "pallets/flask", version: "3.1.3" });

    expect(service.requests[0].method).toBe("POST");
    expect(service.requests[0].path).toBe("/overview");
    expect(JSON.parse(service.requests[0].body)).toEqual({
      repository: "pallets/flask",
      version: "3.1.3",
    });
    expect(reply.files).toEqual([
      {
        path: "src/flask/app.py",
        classes: 1,
        functions: 40,
        calls_within: 12,
        calls_out: 9,
        calls_in: 2,
        link: "https://github.com/pallets/flask/blob/22d9247/src/flask/app.py",
      },
    ]);
    expect(reply.links).toEqual([{ from: "src/flask/app.py", to: "src/flask/helpers.py", calls: 9 }]);
    expect(reply.totals.extra).toBeUndefined();
    expect(reply.left_out).toEqual({ folders: ["examples/"], files: 10, calls: 41 });
    expect(reply.debug).toBeUndefined();
    expect(reply.repository.license.internal_note).toBeUndefined();
  });

  test("an overview whose left-out folders are not text is a 502", async () => {
    service.reply({
      status: 200,
      body: { ...OVERVIEW_REPLY, left_out: { folders: [{ html: "<b>" }], files: 1, calls: 1 } },
    });

    const error = await caught(client().overview({ repository: "pallets/flask", version: "3.1.3" }));

    expect(error.status).toBe(502);
  });

  test("an overview missing its file list is a 502", async () => {
    service.reply({ status: 200, body: { ...OVERVIEW_REPLY, files: undefined } });

    const error = await caught(client().overview({ repository: "pallets/flask", version: "3.1.3" }));

    expect(error).toBeInstanceOf(RetrievalServiceError);
    expect(error.status).toBe(502);
  });

  test("files in other languages are copied with the repository block", async () => {
    const block = {
      ...REPOSITORY_BLOCK,
      other_language_files: [
        { language: "C", files: 214, internal_note: "dropped" },
        { language: "C++", files: 12 },
      ],
    };
    service.reply({ status: 200, body: { status: "already_indexed", repository: block } });

    const reply = await client().startIndexing({ repository: "pallets/flask", version: "3.1.3" });

    expect(reply.repository.other_language_files).toEqual([
      { language: "C", files: 214 },
      { language: "C++", files: 12 },
    ]);
  });

  test("a version indexed before other languages were counted has no count", async () => {
    service.reply({ status: 200, body: { status: "already_indexed", repository: REPOSITORY_BLOCK } });

    const reply = await client().startIndexing({ repository: "pallets/flask", version: "3.1.3" });

    expect(reply.repository.other_language_files).toBeNull();
  });

  test.each([
    [[{ language: { html: "<b>" }, files: 2 }]],
    [[{ language: "C", files: "214" }]],
    [[{ language: "C", files: 0 }]],
    [[{ language: "C", files: 1.5 }]],
    ["C: 214"],
  ])("other-language counts that are not a name and a count are a 502: %j", async (counts) => {
    const block = { ...REPOSITORY_BLOCK, other_language_files: counts };
    service.reply({ status: 200, body: { status: "already_indexed", repository: block } });

    const error = await caught(
      client().startIndexing({ repository: "pallets/flask", version: "3.1.3" }),
    );

    expect(error.status).toBe(502);
  });

  test("an already indexed version comes back with its repository block", async () => {
    const alreadyIndexed = { status: "already_indexed", repository: REPOSITORY_BLOCK };
    service.reply({ status: 200, body: alreadyIndexed });

    const reply = await client().startIndexing({ repository: "pallets/flask", version: "3.1.3" });

    expect(service.requests[0].path).toBe("/index");
    expect(reply.status).toBe("already_indexed");
    expect(reply.repository.license.link).toBe(REPOSITORY_BLOCK.license.link);
    expect(reply.repository.internal_note).toBeUndefined();
  });

  test("a started job comes back with its ID", async () => {
    service.reply({
      status: 202,
      body: { job_id: JOB_ID, status: "running", status_link: `/index-jobs/${JOB_ID}` },
    });

    const reply = await client().startIndexing({ repository: "a/b", version: "1" });

    expect(reply).toEqual({ status: "running", jobId: JOB_ID });
  });

  test("a job's status is fetched by ID and copied without unknown fields", async () => {
    service.reply({ status: 200, body: JOB_REPLY });

    const reply = await client().indexJob(JOB_ID);

    expect(service.requests[0].method).toBe("GET");
    expect(service.requests[0].path).toBe(`/index-jobs/${JOB_ID}`);
    expect(service.requests[0].headers["content-type"]).toBeUndefined();
    expect(reply.extra).toBeUndefined();
    expect(reply.result).toEqual({ commit_id: "096c8d4", chunk_count: 158, cache_hit_count: 0 });
    expect(reply.error).toBeNull();
    expect(reply.progress).toEqual({ embedded: 158, to_embed: 158 });
  });

  test("a failed job keeps its error code and message", async () => {
    const failed = {
      ...JOB_REPLY,
      status: "failed",
      error: { code: "license_not_allowed", message: "No accepted license", trace: "dropped" },
      result: null,
      timings_ms: null,
    };
    service.reply({ status: 200, body: failed });

    const reply = await client().indexJob(JOB_ID);

    expect(reply.error).toEqual({ code: "license_not_allowed", message: "No accepted license" });
    expect(reply.result).toBeNull();
    expect(reply.timings_ms).toBeNull();
  });

  test("a trailing slash on the base URL is ignored", async () => {
    service.reply({ status: 200, body: JOB_REPLY });

    await createRetrievalService({ baseUrl: `${service.url}/`, token: TOKEN }).indexJob(JOB_ID);

    expect(service.requests[0].path).toBe(`/index-jobs/${JOB_ID}`);
  });
});

describe("refusals passed on", () => {
  test("a 400 keeps the service's message, with no Retry-After", async () => {
    service.reply({ status: 400, body: { error: "question must be at most 1000 characters" } });

    const error = await caught(client().ask({ repository: "a/b", version: "1", question: "q" }));

    expect(error).toBeInstanceOf(RetrievalServiceError);
    expect(error.status).toBe(400);
    expect(error.message).toBe("question must be at most 1000 characters");
    expect(error.retryAfterSeconds).toBeNull();
  });

  test("a 409 gets a fixed message; the service's developer message is only logged", async () => {
    const developerMessage = "a/b at 1 has not finished indexing; run index_repository.py";
    service.reply({ status: 409, body: { error: developerMessage } });

    const error = await caught(client().ask({ repository: "a/b", version: "1", question: "q" }));

    expect(error.status).toBe(409);
    expect(error.message).toBe(NOT_SEARCHABLE_MESSAGE);
    expect(error.retryAfterSeconds).toBeNull();
    expect(loggedText()).toContain(developerMessage);
  });

  test("a 503 keeps the service's message and Retry-After", async () => {
    service.reply({
      status: 503,
      headers: { "Retry-After": "41820" },
      body: { error: "Gemini's daily quota is used up; try again after it resets" },
    });

    const error = await caught(client().ask({ repository: "a/b", version: "1", question: "q" }));

    expect(error.status).toBe(503);
    expect(error.message).toBe("Gemini's daily quota is used up; try again after it resets");
    expect(error.retryAfterSeconds).toBe(41820);
  });

  test("a busy indexer's 503 keeps its Retry-After of 60", async () => {
    service.reply({
      status: 503,
      headers: { "Retry-After": "60" },
      body: { error: "Another repository is being indexed; try again shortly" },
    });

    const error = await caught(client().startIndexing({ repository: "a/b", version: "1" }));

    expect(error.status).toBe(503);
    expect(error.retryAfterSeconds).toBe(60);
  });

  test("a 503 with an unreadable Retry-After waits 30 seconds", async () => {
    service.reply({ status: 503, headers: { "Retry-After": "soon" }, body: { error: "Busy" } });

    const error = await caught(client().search({ repository: "a/b", version: "1", query: "q" }));

    expect(error.retryAfterSeconds).toBe(30);
  });

  test("a 502 gets a fixed message, not the service's body", async () => {
    service.reply({ status: 502, body: { error: "something internal" } });

    const error = await caught(client().ask({ repository: "a/b", version: "1", question: "q" }));

    expect(error.status).toBe(502);
    expect(error.message).toBe(REJECTED_ANSWER_MESSAGE);
  });

  test("the service's own 502 stays a rejected answer, which used Gemini", async () => {
    service.reply({ status: 502, body: { error: REJECTED_ANSWER_MESSAGE } });

    const error = await caught(client().ask({ repository: "a/b", version: "1", question: "q" }));

    expect(error.status).toBe(502);
    expect(refusedBeforeGemini(error)).toBe(false);
  });

  test.each([
    ["Render's 502 for a sleeping service", { status: 502, headers: { "x-render-routing": "no-deploy" }, rawBody: "" }],
    ["Render's header even with a JSON error", { status: 502, headers: { "x-render-routing": "no-deploy" }, body: { error: "x" } }],
    ["a 502 page that is not the service's JSON", { status: 502, rawBody: "<html>Bad Gateway</html>" }],
    ["a 503 page that is not the service's JSON", { status: 503, rawBody: "Service Unavailable" }],
    ["a 504 from the host", { status: 504, rawBody: "Gateway Timeout" }],
  ])("%s means the service is starting up: 503, try again in 30 s", async (description, reply) => {
    service.reply(reply);

    const error = await caught(client().ask({ repository: "a/b", version: "1", question: "q" }));

    expect(error.status).toBe(503);
    expect(error.message).toBe(STARTING_UP_MESSAGE);
    expect(error.retryAfterSeconds).toBe(30);
    expect(refusedBeforeGemini(error)).toBe(true);
    expect(loggedText()).toContain("/ask was not running");
  });

  test("a sleeping service refuses searches, overviews, and indexing the same way", async () => {
    const sleeping = { status: 502, headers: { "x-render-routing": "no-deploy" }, rawBody: "" };
    const calls = [
      () => client().search({ repository: "a/b", version: "1", query: "q" }),
      () => client().overview({ repository: "a/b", version: "1" }),
      () => client().startIndexing({ repository: "a/b", version: "1" }),
      () => client().indexJob(JOB_ID),
    ];
    for (const makeCall of calls) {
      service.reply(sleeping);
      const error = await caught(makeCall());
      expect(error.status).toBe(503);
      expect(error.message).toBe(STARTING_UP_MESSAGE);
    }
  });

  test("an unknown job is a 404 with a fixed message", async () => {
    service.reply({ status: 404, body: { error: "No such indexing job" } });

    const error = await caught(client().indexJob(JOB_ID));

    expect(error.status).toBe(404);
    expect(error.message).toBe(UNKNOWN_JOB_MESSAGE);
  });

  test.each(["../health", "ABCDEF0123456789ABCDEF0123456789", "123", 42])(
    "a malformed job ID %p is refused without calling the service",
    async (jobId) => {
      const error = await caught(client().indexJob(jobId));

      expect(error.status).toBe(404);
      expect(service.requests).toHaveLength(0);
    },
  );
});

describe("unexpected replies", () => {
  test("a refused token is a 502, logged with status and path but never the body", async () => {
    const refusal = { error: "A valid service token is required, secret-body" };
    service.reply({ status: 401, body: refusal });

    const error = await caught(client().ask({ repository: "a/b", version: "1", question: "q" }));

    expect(error.status).toBe(502);
    expect(error.message).toBe(UNEXPECTED_REPLY_MESSAGE);
    expect(loggedText()).toContain("POST /ask");
    expect(loggedText()).toContain("status 401");
    expect(loggedText()).not.toContain("secret-body");
  });

  test("a 404 outside the job route means a wrong URL, not an unknown job", async () => {
    service.reply({ status: 404, body: { error: "Not found" } });

    const error = await caught(client().ask({ repository: "a/b", version: "1", question: "q" }));

    expect(error.status).toBe(502);
    expect(error.message).toBe(UNEXPECTED_REPLY_MESSAGE);
  });

  test("a success that is not JSON is a 502", async () => {
    service.reply({ status: 200, rawBody: "<html>proxy error</html>" });

    const error = await caught(client().search({ repository: "a/b", version: "1", query: "q" }));

    expect(error.status).toBe(502);
  });

  test("a success missing a list is a 502", async () => {
    service.reply({ status: 200, body: { ...SEARCH_REPLY, results: undefined } });

    const error = await caught(client().search({ repository: "a/b", version: "1", query: "q" }));

    expect(error.status).toBe(502);
  });

  test("an error reply without a message is a 502", async () => {
    service.reply({ status: 400, rawBody: "Bad Request" });

    const error = await caught(client().ask({ repository: "a/b", version: "1", question: "q" }));

    expect(error.status).toBe(502);
  });

  test("a start reply with an unknown status is a 502", async () => {
    service.reply({ status: 202, body: { job_id: "not-an-id", status: "running" } });

    const error = await caught(client().startIndexing({ repository: "a/b", version: "1" }));

    expect(error.status).toBe(502);
  });
});

describe("the service out of reach", () => {
  test("a reply slower than the time limit is a 503 with Retry-After 30", async () => {
    service.reply({ status: 200, body: SEARCH_REPLY, delayMilliseconds: 500 });
    const impatient = client({ timeouts: { search: 50, index: 50, ask: 50 } });

    const error = await caught(impatient.search({ repository: "a/b", version: "1", query: "q" }));

    expect(error.status).toBe(503);
    expect(error.message).toBe(UNAVAILABLE_MESSAGE);
    expect(error.retryAfterSeconds).toBe(30);
    expect(loggedText()).toContain("POST /search failed");
    expect(loggedText()).not.toContain(TOKEN);
  });

  test("a service that is not running is a 503", async () => {
    const url = service.url;
    await service.close();

    const error = await caught(
      createRetrievalService({ baseUrl: url, token: TOKEN }).indexJob(JOB_ID),
    );

    expect(error.status).toBe(503);
    service = await startFakeService();
  });

  test("a redirect is never followed, so the token goes nowhere else", async () => {
    const elsewhere = await startFakeService();
    service.reply({ status: 302, headers: { Location: `${elsewhere.url}/ask` }, rawBody: "" });

    const error = await caught(client().ask({ repository: "a/b", version: "1", question: "q" }));

    expect(error.status).toBe(503);
    expect(elsewhere.requests).toHaveLength(0);
    await elsewhere.close();
  });
});

function startFakeService() {
  const requests = [];
  let nextReply = { status: 500, rawBody: "no reply set" };

  const server = http.createServer((incoming, outgoing) => {
    const chunks = [];
    incoming.on("data", (chunk) => chunks.push(chunk));
    incoming.on("end", () => {
      requests.push({
        method: incoming.method,
        path: incoming.url,
        headers: incoming.headers,
        body: Buffer.concat(chunks).toString("utf8"),
      });
      const reply = nextReply;
      const send = () => {
        const isJson = reply.body !== undefined;
        outgoing.writeHead(reply.status, {
          ...(isJson ? { "Content-Type": "application/json" } : {}),
          ...(reply.headers || {}),
        });
        outgoing.end(isJson ? JSON.stringify(reply.body) : reply.rawBody);
      };
      if (reply.delayMilliseconds) {
        setTimeout(send, reply.delayMilliseconds);
      } else {
        send();
      }
    });
  });

  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({
        url: `http://127.0.0.1:${port}`,
        requests,
        reply(reply) {
          nextReply = reply;
        },
        close() {
          server.closeAllConnections();
          return new Promise((done) => server.close(() => done()));
        },
      });
    });
  });
}

describe("refreshing an indexed version", () => {
  test("asks the service to refresh, and an unmoved version is up to date with its block", async () => {
    service.reply({ status: 200, body: { status: "up_to_date", repository: REPOSITORY_BLOCK } });

    const reply = await client().refreshIndexing({ repository: "pallets/flask", version: "main" });

    expect(JSON.parse(service.requests[0].body)).toEqual({
      repository: "pallets/flask",
      version: "main",
      refresh: true,
    });
    expect(service.requests[0].path).toBe("/index");
    expect(reply.status).toBe("up_to_date");
    expect(reply.repository.commit_id).toBe(REPOSITORY_BLOCK.commit_id);
  });

  test("a moved version starts a job", async () => {
    service.reply({ status: 202, body: { job_id: JOB_ID, status: "running", status_link: "x" } });

    const reply = await client().refreshIndexing({ repository: "pallets/flask", version: "main" });

    expect(reply).toEqual({ status: "running", jobId: JOB_ID });
  });

  test("a tag or branch that no longer exists gets its own message", async () => {
    service.reply({ status: 409, body: { error: "main is no longer a tag or branch of a/b" } });

    const error = await caught(client().refreshIndexing({ repository: "a/b", version: "main" }));

    expect(error.status).toBe(409);
    expect(error.message).toBe(VERSION_GONE_MESSAGE);
    expect(loggedText()).toContain("main is no longer a tag or branch of a/b");
  });

  test("GitHub out of reach passes on the service's message and wait", async () => {
    const message = "GitHub could not be reached to check for changes; try again shortly";
    service.reply({ status: 503, headers: { "Retry-After": "30" }, body: { error: message } });

    const error = await caught(client().refreshIndexing({ repository: "a/b", version: "main" }));

    expect(error.status).toBe(503);
    expect(error.message).toBe(message);
    expect(error.retryAfterSeconds).toBe(30);
  });

  test("an up_to_date status is accepted only from a refresh", async () => {
    service.reply({ status: 200, body: { status: "up_to_date", repository: REPOSITORY_BLOCK } });

    const error = await caught(client().startIndexing({ repository: "a/b", version: "1" }));

    expect(error.status).toBe(502);
  });
});

describe("checking the service's health", () => {
  test("a healthy service answers GET /health", async () => {
    service.reply({ status: 200, body: { status: "ok" } });

    const health = await client().checkHealth();

    expect(health.healthy).toBe(true);
    expect(service.requests[0]).toMatchObject({ method: "GET", path: "/health" });
  });

  test("a service that is down is reported as not healthy, never thrown", async () => {
    await service.close();

    const health = await client().checkHealth();

    expect(health.healthy).toBe(false);
    expect(loggedText()).toContain("GET /health failed");
  });

  test("a service that answers too slowly is not healthy", async () => {
    service.reply({ status: 200, body: { status: "ok" }, delayMilliseconds: 200 });

    const health = await client({ timeouts: { ...TIMEOUT_MILLISECONDS, wake: 50 } }).checkHealth();

    expect(health.healthy).toBe(false);
  });
});
