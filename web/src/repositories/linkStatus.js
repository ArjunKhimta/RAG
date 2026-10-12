export const LinkStatus = Object.freeze({
  INDEXING: "indexing",
  READY: "ready",
  WAITING_FOR_QUOTA: "waiting_for_quota",
  INTERRUPTED: "interrupted",
  FAILED: "failed",
});

export const RefreshStatus = Object.freeze({
  RUNNING: "running",
  WAITING_FOR_QUOTA: "waiting_for_quota",
  FAILED: "failed",
  INTERRUPTED: "interrupted",
});

const STATUS_LABELS = Object.freeze({
  [LinkStatus.INDEXING]: "Indexing",
  [LinkStatus.READY]: "Ready",
  [LinkStatus.WAITING_FOR_QUOTA]: "Paused until the daily quota resets",
  [LinkStatus.INTERRUPTED]: "Interrupted",
  [LinkStatus.FAILED]: "Failed",
});

const STAGE_DESCRIPTIONS = Object.freeze({
  cloning: "Downloading the repository from GitHub",
  chunking: "Splitting the code into functions and classes",
  "call graph": "Working out which functions call which",
  embedding: "Turning the code into searchable vectors",
});

const RETRYABLE_ERROR_CODES = new Set(["clone_failed", "github_rate_limited", "unexpected"]);

const GITHUB_ADDRESS_PATTERN = /^(?:https?:\/\/)?(?:www\.)?github\.com\//i;

export function statusLabel(status) {
  return Object.hasOwn(STATUS_LABELS, status) ? STATUS_LABELS[status] : status;
}

export function stageDescription(stage) {
  if (stage === null || !Object.hasOwn(STAGE_DESCRIPTIONS, stage)) {
    return "Starting";
  }
  return STAGE_DESCRIPTIONS[stage];
}

export function resumeAvailability(link, nowMilliseconds) {
  if (link.status === LinkStatus.INTERRUPTED) {
    return { canResume: true, resumeAfter: null };
  }
  if (link.status === LinkStatus.FAILED) {
    const code = link.error === null ? null : link.error.code;
    return { canResume: RETRYABLE_ERROR_CODES.has(code), resumeAfter: null };
  }
  if (link.status === LinkStatus.WAITING_FOR_QUOTA) {
    if (link.resume_after === null) {
      return { canResume: true, resumeAfter: null };
    }
    const waitMilliseconds = new Date(link.resume_after).getTime() - nowMilliseconds;
    return { canResume: waitMilliseconds <= 0, resumeAfter: link.resume_after };
  }
  return { canResume: false, resumeAfter: null };
}

export function refreshOf(link) {
  return link.refresh ?? null;
}

export function isRefreshRunning(link) {
  const refresh = refreshOf(link);
  const isRunning = refresh !== null && refresh.status === RefreshStatus.RUNNING;
  return link.status === LinkStatus.READY && isRunning;
}

export function refreshAvailability(link, nowMilliseconds) {
  const refresh = refreshOf(link);
  if (link.status !== LinkStatus.READY || isRefreshRunning(link)) {
    return { canRefresh: false, resumeAfter: null };
  }
  if (refresh !== null && refresh.status === RefreshStatus.WAITING_FOR_QUOTA) {
    if (refresh.resume_after === null) {
      return { canRefresh: true, resumeAfter: null };
    }
    const waitMilliseconds = new Date(refresh.resume_after).getTime() - nowMilliseconds;
    return { canRefresh: waitMilliseconds <= 0, resumeAfter: refresh.resume_after };
  }
  return { canRefresh: true, resumeAfter: null };
}

export function normalizeRepositoryInput(text) {
  return text
    .trim()
    .replace(GITHUB_ADDRESS_PATTERN, "")
    .replace(/\/+$/, "")
    .replace(/\.git$/i, "");
}
