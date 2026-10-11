export const POLICY_UPDATED = "12 October 2026";

export const PRIVACY_CONTACT_URL = "https://github.com/ArjunKhimta/RAG/issues";

export const VERCEL_PRIVACY_URL = "https://vercel.com/legal/privacy-policy";

export const RENDER_PRIVACY_URL = "https://render.com/privacy";

export const SERVER_LOCATION = "Singapore";

export const DATABASE_LOCATION = "Mumbai, India";

export const VERCEL_LOG_RETENTION = "1 hour";

export const RENDER_LOG_RETENTION = "7 days";

export const GEMINI_TERMS_URL = "https://ai.google.dev/gemini-api/terms";

export const GEMINI_TERMS_EFFECTIVE = "23 March 2026";

export const GITHUB_PRIVACY_URL =
  "https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement";

export const MONGODB_PRIVACY_URL = "https://www.mongodb.com/legal/privacy/privacy-policy";

export const GITHUB_AUTHORIZED_APPS_URL = "https://github.com/settings/applications";

export const STORED_DATA = Object.freeze([
  {
    what: "Your account: GitHub user ID, username, display name, and avatar link, with the dates it was created and last signed in, and a counter that signing out raises so every old session stops working",
    why: "To know who you are and keep your repositories and conversations separate from everyone else's",
    howLong: "Until you delete your account; the name and avatar are refreshed from GitHub each time you sign in",
  },
  {
    what: "Your linked repositories: name, version, and indexing status",
    why: "To show your list and the progress of indexing",
    howLong: "Until you remove the repository or delete your account",
  },
  {
    what: "Your conversations: each question you ask, its answer with the cited code snippets, and a title made from the first question",
    why: "So you can read your earlier answers again",
    howLong: "Each question and answer 90 days after you asked it, and a conversation 90 days after you last used it, or sooner when you delete it or your account",
  },
  {
    what: "Daily usage counts: how many questions, searches, and indexing requests you made each day (numbers only, never what you asked)",
    why: "To apply the daily limits that keep the service within its free quotas",
    howLong: "30 days, or until you delete your account",
  },
]);

export const NOT_STORED = Object.freeze([
  "your email address or GitHub password",
  "your GitHub access token: it is used once to read your public profile, then thrown away",
  "your private repositories: the site asks GitHub for no permissions beyond your public profile",
  "your searches: results are shown and not kept",
]);

export const COOKIES = Object.freeze([
  {
    name: "session",
    purpose: "Keeps you signed in. It holds only your user ID, the sign-out counter, and when it was issued and expires, signed so it cannot be forged, and the page's scripts cannot read it",
    lifetime: "7 days, or until you sign out",
  },
  {
    name: "sign_in_attempt",
    purpose: "Protects the GitHub sign-in from being forged or replayed while you are signing in",
    lifetime: "10 minutes, deleted as soon as sign-in finishes",
  },
]);
