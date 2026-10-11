const BASE_LINK = Object.freeze({
  id: "link-flask",
  repository: "pallets/flask",
  version: "3.1.3",
  status: "ready",
  stage: null,
  progress: null,
  error: null,
  resume_after: null,
  indexed: {
    commit_id: "22d924701a6ae2e4cd01e9a15bbaf3946094af65",
    license: {
      spdx_id: "BSD-3-Clause",
      name: "BSD 3-Clause \"New\" or \"Revised\" License",
      path: "LICENSE.txt",
      link: "https://github.com/pallets/flask/blob/22d924701a6ae2e4cd01e9a15bbaf3946094af65/LICENSE.txt",
    },
  },
  created_at: "2026-10-11T10:00:00.000Z",
  updated_at: "2026-10-11T10:00:00.000Z",
});

export function readyLink(overrides = {}) {
  return { ...BASE_LINK, ...overrides };
}

export function indexingLink(overrides = {}) {
  return {
    ...BASE_LINK,
    id: "link-itsdangerous",
    repository: "pallets/itsdangerous",
    version: "2.2.0",
    status: "indexing",
    stage: "cloning",
    indexed: null,
    ...overrides,
  };
}

export function failedLink(code, message, overrides = {}) {
  return {
    ...BASE_LINK,
    id: `link-${code}`,
    repository: "someone/missing",
    version: "1.0",
    status: "failed",
    error: { code, message },
    indexed: null,
    ...overrides,
  };
}

export function waitingLink(resumeAfter, overrides = {}) {
  return {
    ...BASE_LINK,
    id: "link-waiting",
    repository: "pallets/click",
    version: "8.3.0",
    status: "waiting_for_quota",
    progress: { embedded: 800, to_embed: 1200 },
    error: { code: "waiting_for_quota", message: "The daily embedding quota is used up" },
    resume_after: resumeAfter,
    indexed: null,
    ...overrides,
  };
}

export function replies(...sequence) {
  let index = 0;
  return () => {
    const reply = sequence[Math.min(index, sequence.length - 1)];
    index += 1;
    return reply;
  };
}
