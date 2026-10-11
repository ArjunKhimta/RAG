const COMMIT = "22d924701a6ae2e4cd01e9a15bbaf3946094af65";

const BLOB = `https://github.com/pallets/flask/blob/${COMMIT}`;

export const REPOSITORY_BLOCK = Object.freeze({
  name: "pallets/flask",
  version: "3.1.3",
  commit_id: COMMIT,
  license: {
    spdx_id: "BSD-3-Clause",
    name: "BSD 3-Clause \"New\" or \"Revised\" License",
    path: "LICENSE.txt",
    link: `${BLOB}/LICENSE.txt`,
  },
});

export function foundReply(overrides = {}) {
  return {
    found_answer: true,
    answer:
      "Flask builds a signing serializer from the app's secret key [1]. The serializer signs the session with HMAC [1].",
    sentences: [
      {
        text: "Flask builds a signing serializer from the app's secret key.",
        citations: [{ source: 1, start_line: 303, end_line: 314 }],
      },
      {
        text: "The serializer signs the session with HMAC.",
        citations: [
          { source: 1, start_line: 315, end_line: 320 },
          { source: 2, start_line: 284, end_line: 298 },
        ],
      },
      { text: "Sessions are stored in a cookie.", citations: [] },
    ],
    sources: [
      {
        number: 1,
        file_path: "src/flask/sessions.py",
        kind: "method",
        qualified_name: "SecureCookieSessionInterface.get_signing_serializer",
        start_line: 303,
        end_line: 320,
        link: `${BLOB}/src/flask/sessions.py#L303-L320`,
      },
      {
        number: 2,
        file_path: "src/flask/sessions.py",
        kind: "class",
        qualified_name: "SecureCookieSessionInterface",
        start_line: 284,
        end_line: 380,
        link: `${BLOB}/src/flask/sessions.py#L284-L380`,
      },
    ],
    snippets: [
      {
        source: 1,
        start_line: 303,
        end_line: 314,
        link: `${BLOB}/src/flask/sessions.py#L303-L314`,
        is_outline: false,
        more_lines: 0,
        lines: [
          { number: 303, text: "    def get_signing_serializer(self, app: Flask) -> URLSafeTimedSerializer | None:" },
          { number: 304, text: "        if not app.secret_key:" },
        ],
      },
      {
        source: 1,
        start_line: 315,
        end_line: 320,
        link: `${BLOB}/src/flask/sessions.py#L315-L320`,
        is_outline: false,
        more_lines: 3,
        lines: [{ number: 315, text: "        return URLSafeTimedSerializer(" }],
      },
      {
        source: 2,
        start_line: 284,
        end_line: 298,
        link: `${BLOB}/src/flask/sessions.py#L284-L298`,
        is_outline: true,
        more_lines: 0,
        lines: [
          { number: null, text: "class SecureCookieSessionInterface(SessionInterface):" },
          { number: null, text: "    def get_signing_serializer(self, app): ..." },
        ],
      },
    ],
    repository: REPOSITORY_BLOCK,
    usage: { embedding: "from the cache", answer_requests: 1, prompt_tokens: 2213, output_tokens: 150 },
    timings_ms: { checks: 35, embed: 32, search: 45, answer: 1700 },
    ...overrides,
  };
}

export function notFoundReply() {
  return {
    found_answer: false,
    answer: "No code in this repository version matched the question.",
    sentences: [],
    sources: [],
    snippets: [],
    repository: REPOSITORY_BLOCK,
    usage: null,
    timings_ms: {},
  };
}

export function message(id, question, reply = foundReply(), overrides = {}) {
  return {
    id,
    question,
    exclude_tests: false,
    reply,
    created_at: "2026-10-11T10:05:00.000Z",
    ...overrides,
  };
}

export function conversation(overrides = {}) {
  return {
    id: "conversation-1",
    repository: "pallets/flask",
    version: "3.1.3",
    title: "How does Flask sign the session cookie?",
    created_at: "2026-10-11T10:04:00.000Z",
    updated_at: "2026-10-11T10:05:00.000Z",
    ...overrides,
  };
}

export function searchReply(results) {
  return {
    results,
    repository: REPOSITORY_BLOCK,
    usage: { embedding: "from the cache" },
    timings_ms: { checks: 31, embed: 32, search: 44 },
  };
}

export function searchResult(rank, overrides = {}) {
  return {
    rank,
    file_path: "src/flask/sessions.py",
    kind: "method",
    qualified_name: "SecureCookieSessionInterface.get_signing_serializer",
    start_line: 303,
    end_line: 320,
    is_test_file: false,
    score: 0.81,
    link: `${BLOB}/src/flask/sessions.py#L303-L320`,
    snippet: {
      is_outline: false,
      more_lines: 6,
      lines: [{ number: 303, text: "    def get_signing_serializer(self, app):" }],
    },
    ...overrides,
  };
}
