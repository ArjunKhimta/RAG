import { REPOSITORY_BLOCK } from "./answers.js";

const BLOB = `https://github.com/pallets/flask/blob/${REPOSITORY_BLOCK.commit_id}`;

export function overviewFile(path, overrides = {}) {
  return {
    path,
    classes: 0,
    functions: 1,
    calls_within: 0,
    calls_out: 0,
    calls_in: 0,
    link: `${BLOB}/${path}`,
    ...overrides,
  };
}

export function flaskOverview() {
  return {
    repository: REPOSITORY_BLOCK,
    files: [
      overviewFile("src/flask/app.py", { classes: 1, functions: 40, calls_within: 12, calls_out: 9, calls_in: 2 }),
      overviewFile("src/flask/helpers.py", { functions: 20, calls_within: 3, calls_out: 2, calls_in: 11 }),
      overviewFile("src/flask/sessions.py", { classes: 4, functions: 18, calls_out: 2 }),
      overviewFile("src/flask/py.typed", { functions: 0 }),
    ],
    links: [
      { from: "src/flask/app.py", to: "src/flask/helpers.py", calls: 9 },
      { from: "src/flask/helpers.py", to: "src/flask/app.py", calls: 2 },
      { from: "src/flask/sessions.py", to: "src/flask/helpers.py", calls: 2 },
    ],
    totals: {
      files: 4,
      classes: 5,
      functions: 78,
      calls: 28,
      calls_between_files: 13,
      calls_within_files: 15,
    },
    timings_ms: { checks: 31, "summarize call graph": 19 },
  };
}
