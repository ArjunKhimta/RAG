import { emptyReply, jsonReply } from "./fakeApi.js";

export const OCTOCAT = Object.freeze({
  id: "665f1c2a9b1e8a0012345678",
  login: "octocat",
  name: "The Octocat",
  avatar_url: "https://avatars.githubusercontent.com/u/583231?v=4",
});

export const SIGNED_IN = jsonReply(200, OCTOCAT);

export const SIGNED_OUT = jsonReply(401, { error: "Sign in first" });

export const USAGE = jsonReply(200, {
  day: "2026-10-11",
  resets_at: "2026-10-12T07:00:00.000Z",
  questions: { used: 3, limit: 30 },
  searches: { used: 12, limit: 100 },
  indexing_requests: { used: 1, limit: 2 },
  linked_repositories: { used: 2, limit: 5 },
});

export const NO_CONTENT = emptyReply(204);
