/**
 * The signed-in person's repositories (every route needs a session):
 * - `POST /api/repositories` `{repository, version}`: add a link and ask the service to index the
 *   version: 201 when it is already searchable, 202 when indexing started or was already running,
 *   200 with the existing link when the person already has it (no service call); the service's
 *   400 (bad name or version) and 503 (busy with another job, `Retry-After: 60`) pass through and
 *   nothing is saved
 * - `GET /api/repositories`: the person's links, newest first, as last seen (no service calls)
 * - `GET /api/repositories/:id`: one link; while it is indexing, the job's progress is fetched
 *   from the service and saved first, so the site polls this route to show progress
 * - `POST /api/repositories/:id/resume`: start indexing again where it stopped (the service
 *   reuses every embedding already made); refused with 409 when there is nothing to resume, when
 *   the failure will recur, or before the quota resets (with `Retry-After`)
 * - `POST /api/repositories/:id/search` `{query, limit?, exclude_tests?}`: search a ready link's
 *   version with the service's vector search; results are shown, not kept in history
 * - `DELETE /api/repositories/:id`: remove the link only; the shared index stays
 *
 * Limits (`limits.js`): at most 5 links at a time (409 to add a sixth); 2 new indexing requests a
 * day (adding or resuming; the slot is given back when the version was already indexed or the
 * service refused), and 100 searches a day (given back when the service refused before Gemini did
 * any work), both 429 with `Retry-After` until midnight Pacific. Reading a link whose job finished
 * asks the service once more for the license, but uses no slot. Because the slot is taken before
 * asking the service, a person who has used both of today's slots cannot add even an already
 * indexed version until midnight.
 *
 * Repository names are lowercased before anything else, because GitHub treats `Pallets/Flask` and
 * `pallets/flask` as the same repository and the service would index them twice. A link ID that is
 * malformed, unknown, or another person's is the same 404, so IDs reveal nothing.
 */

const express = require("express");

const { ApiError } = require("./apiError");
const {
  indexingState,
  readyState,
  resumeDecision,
  stateFromJob,
  unknownJobState,
  JobStatus,
} = require("./linkStates");
const { LinkStatus } = require("./repositoryLinks");
const {
  badRequest,
  booleanField,
  requireFields,
  textField,
  wholeNumberField,
} = require("./requestFields");
const {
  UNEXPECTED_REPLY_MESSAGE,
  RetrievalServiceError,
  refusedBeforeGemini,
} = require("./retrievalService");
const { UsageKind } = require("./usage");

const OK_STATUS = 200;

const CREATED_STATUS = 201;

const ACCEPTED_STATUS = 202;

const NO_CONTENT_STATUS = 204;

const NOT_FOUND_STATUS = 404;

const CONFLICT_STATUS = 409;

const BAD_GATEWAY_STATUS = 502;

const ALREADY_INDEXED = "already_indexed";

const LINK_REQUEST_FIELDS = ["repository", "version"];

const SEARCH_REQUEST_FIELDS = ["query"];

const SEARCH_OPTIONAL_FIELDS = ["limit", "exclude_tests"];

const LOWEST_SEARCH_LIMIT = 1;

const HIGHEST_SEARCH_LIMIT = 20;

const NOT_READY_MESSAGE = "This repository is not ready to search yet";

const MAX_REPOSITORY_LENGTH = 140;

const MAX_VERSION_LENGTH = 100;

const REPOSITORY_RULE = "repository must be owner/repo, as on GitHub";

const VERSION_RULE = "version must be a tag, a branch without '/', or a commit ID";

const UNKNOWN_LINK_MESSAGE = "No such repository";

const NOTHING_TO_RESUME_MESSAGE = "This repository is not waiting to be indexed";

const PERMANENT_FAILURE_MESSAGE =
  "Indexing this repository will fail the same way again; remove it instead";

const TOO_EARLY_MESSAGE = "Indexing can resume after the daily embedding quota resets";

/**
 * @param {{
 *   retrievalService: ReturnType<typeof import("./retrievalService").createRetrievalService>,
 *   repositoryLinks: ReturnType<typeof import("./repositoryLinks").createMongoRepositoryLinkStore>,
 *   dailyLimits: ReturnType<typeof import("./usage").createDailyLimits>,
 *   limits: typeof import("./limits").DEFAULT_LIMITS,
 *   requireUser: import("express").RequestHandler,
 *   now?: () => number,
 * }} dependencies
 */
function createRepositoryRouter({
  retrievalService,
  repositoryLinks,
  dailyLimits,
  limits,
  requireUser,
  now = Date.now,
}) {
  const router = express.Router();
  router.use(requireUser);

  router.post("/", async (request, response) => {
    const { repository, version } = readLinkRequest(request.body);
    const existing = await repositoryLinks.findByTarget(request.user.id, repository, version);
    if (existing !== null) {
      response.status(OK_STATUS).json(publicLink(existing));
      return;
    }
    if ((await repositoryLinks.countForUser(request.user.id)) >= limits.linkedRepositories) {
      throw new ApiError(CONFLICT_STATUS, tooManyRepositoriesMessage(limits.linkedRepositories));
    }
    const state = await startCountedIndexing(request.user.id, repository, version);
    const link = await repositoryLinks.create({
      userId: request.user.id,
      repository,
      version,
      state,
      now: new Date(now()),
    });
    const status = link.status === LinkStatus.READY ? CREATED_STATUS : ACCEPTED_STATUS;
    response.status(status).json(publicLink(link));
  });

  router.get("/", async (request, response) => {
    const links = await repositoryLinks.listForUser(request.user.id);
    response.json({ repositories: links.map(publicLink) });
  });

  router.get("/:linkId", async (request, response) => {
    let link = await findOwnedLink(request);
    if (link.status === LinkStatus.INDEXING) {
      link = await saveState(request, await currentJobState(link));
    }
    response.json(publicLink(link));
  });

  router.post("/:linkId/resume", async (request, response) => {
    const link = await findOwnedLink(request);
    const decision = resumeDecision(link, now());
    if (!decision.allowed) {
      throw resumeRefusal(decision);
    }
    const state = await startCountedIndexing(request.user.id, link.repository, link.version);
    const updated = await saveState(request, state);
    const status = updated.status === LinkStatus.READY ? OK_STATUS : ACCEPTED_STATUS;
    response.status(status).json(publicLink(updated));
  });

  router.post("/:linkId/search", async (request, response) => {
    const search = readSearchRequest(request.body);
    const link = await findOwnedLink(request);
    if (link.status !== LinkStatus.READY) {
      throw new ApiError(CONFLICT_STATUS, NOT_READY_MESSAGE);
    }
    const ticket = await dailyLimits.take(request.user.id, UsageKind.SEARCHES);
    let results;
    try {
      results = await retrievalService.search({
        repository: link.repository,
        version: link.version,
        ...search,
      });
    } catch (error) {
      if (refusedBeforeGemini(error)) {
        await dailyLimits.giveBack(ticket);
      }
      throw error;
    }
    response.json(results);
  });

  router.delete("/:linkId", async (request, response) => {
    const removed = await repositoryLinks.remove(request.user.id, request.params.linkId);
    if (!removed) {
      throw new ApiError(NOT_FOUND_STATUS, UNKNOWN_LINK_MESSAGE);
    }
    response.status(NO_CONTENT_STATUS).end();
  });

  /** @param {import("express").Request} request */
  async function findOwnedLink(request) {
    const link = await repositoryLinks.findForUser(request.user.id, request.params.linkId);
    if (link === null) {
      throw new ApiError(NOT_FOUND_STATUS, UNKNOWN_LINK_MESSAGE);
    }
    return link;
  }

  /**
   * @param {import("express").Request} request
   * @param {import("./repositoryLinks").LinkState} state
   */
  async function saveState(request, state) {
    const link = await repositoryLinks.saveState(
      request.user.id,
      request.params.linkId,
      state,
      new Date(now()),
    );
    if (link === null) {
      throw new ApiError(NOT_FOUND_STATUS, UNKNOWN_LINK_MESSAGE);
    }
    return link;
  }

  /**
   * Start indexing on the person's behalf, using one of today's indexing slots. The slot is given
   * back when the version was already indexed (no work) or the service refused; the index route
   * never uses Gemini while the request is open, so any refusal means no work was done.
   * @param {string} userId
   * @param {string} repository
   * @param {string} version
   */
  async function startCountedIndexing(userId, repository, version) {
    const ticket = await dailyLimits.take(userId, UsageKind.INDEXING_REQUESTS);
    let state;
    try {
      state = await startIndexing(repository, version);
    } catch (error) {
      await dailyLimits.giveBack(ticket);
      throw error;
    }
    if (state.status === LinkStatus.READY) {
      await dailyLimits.giveBack(ticket);
    }
    return state;
  }

  /**
   * Ask the service to index a version and return the link state its answer means.
   * @param {string} repository
   * @param {string} version
   */
  async function startIndexing(repository, version) {
    const started = await retrievalService.startIndexing({ repository, version });
    if (started.status === ALREADY_INDEXED) {
      return readyState(started.repository);
    }
    return indexingState(started.jobId);
  }

  /** @param {import("./repositoryLinks").RepositoryLink} link */
  async function currentJobState(link) {
    let job;
    try {
      job = await retrievalService.indexJob(link.jobId);
    } catch (error) {
      if (error instanceof RetrievalServiceError && error.status === NOT_FOUND_STATUS) {
        return unknownJobState(link.jobId);
      }
      throw error;
    }
    if (job.status === JobStatus.SUCCEEDED) {
      return startIndexing(link.repository, link.version);
    }
    const state = stateFromJob(job);
    if (state === null) {
      throw new ApiError(BAD_GATEWAY_STATUS, UNEXPECTED_REPLY_MESSAGE);
    }
    return state;
  }

  return router;
}

/**
 * Check the request body's shape; the retrieval service checks the name and version themselves.
 * @param {unknown} body
 */
function readLinkRequest(body) {
  const { repository, version } = requireFields(body, LINK_REQUEST_FIELDS);
  if (!isTextUpTo(repository, MAX_REPOSITORY_LENGTH)) {
    throw badRequest(REPOSITORY_RULE);
  }
  if (!isTextUpTo(version, MAX_VERSION_LENGTH)) {
    throw badRequest(VERSION_RULE);
  }
  return { repository: repository.toLowerCase(), version };
}

/**
 * @param {unknown} body
 * @returns {{ query: string, limit?: number, excludeTests?: boolean }}
 */
function readSearchRequest(body) {
  const fields = requireFields(body, SEARCH_REQUEST_FIELDS, SEARCH_OPTIONAL_FIELDS);
  const search = { query: textField(fields.query, "query") };
  if ("limit" in fields) {
    search.limit = wholeNumberField(
      fields.limit,
      "limit",
      LOWEST_SEARCH_LIMIT,
      HIGHEST_SEARCH_LIMIT,
    );
  }
  if ("exclude_tests" in fields) {
    search.excludeTests = booleanField(fields.exclude_tests, "exclude_tests");
  }
  return search;
}

/**
 * @param {unknown} value
 * @param {number} maxLength
 */
function isTextUpTo(value, maxLength) {
  return typeof value === "string" && value.length > 0 && value.length <= maxLength;
}

/** @param {number} limit */
function tooManyRepositoriesMessage(limit) {
  return `You have ${limit} repositories; remove one before adding another`;
}

/** @param {{ allowed: false, reason: string, retryAfterSeconds?: number }} decision */
function resumeRefusal(decision) {
  if (decision.reason === "too_early") {
    return new ApiError(CONFLICT_STATUS, TOO_EARLY_MESSAGE, decision.retryAfterSeconds);
  }
  if (decision.reason === "permanent") {
    return new ApiError(CONFLICT_STATUS, PERMANENT_FAILURE_MESSAGE);
  }
  return new ApiError(CONFLICT_STATUS, NOTHING_TO_RESUME_MESSAGE);
}

/**
 * What the site sees of a link: no user ID and no job ID.
 * @param {import("./repositoryLinks").RepositoryLink} link
 */
function publicLink(link) {
  return {
    id: link.id,
    repository: link.repository,
    version: link.version,
    status: link.status,
    stage: link.stage,
    progress: link.progress,
    error: link.error,
    resume_after: link.resumeAfter === null ? null : link.resumeAfter.toISOString(),
    indexed: link.indexed,
    created_at: link.createdAt.toISOString(),
    updated_at: link.updatedAt.toISOString(),
  };
}

module.exports = {
  NOT_READY_MESSAGE,
  NOTHING_TO_RESUME_MESSAGE,
  PERMANENT_FAILURE_MESSAGE,
  REPOSITORY_RULE,
  TOO_EARLY_MESSAGE,
  UNKNOWN_LINK_MESSAGE,
  VERSION_RULE,
  createRepositoryRouter,
  tooManyRepositoriesMessage,
};
