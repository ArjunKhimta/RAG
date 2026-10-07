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
 * - `DELETE /api/repositories/:id`: remove the link only; the shared index stays
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
const { UNEXPECTED_REPLY_MESSAGE, RetrievalServiceError } = require("./retrievalService");

const OK_STATUS = 200;

const CREATED_STATUS = 201;

const ACCEPTED_STATUS = 202;

const NO_CONTENT_STATUS = 204;

const BAD_REQUEST_STATUS = 400;

const NOT_FOUND_STATUS = 404;

const CONFLICT_STATUS = 409;

const BAD_GATEWAY_STATUS = 502;

const ALREADY_INDEXED = "already_indexed";

const REQUEST_FIELDS = ["repository", "version"];

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
 *   requireUser: import("express").RequestHandler,
 *   now?: () => number,
 * }} dependencies
 */
function createRepositoryRouter({ retrievalService, repositoryLinks, requireUser, now = Date.now }) {
  const router = express.Router();
  router.use(requireUser);

  router.post("/", async (request, response) => {
    const { repository, version } = readLinkRequest(request.body);
    const existing = await repositoryLinks.findByTarget(request.user.id, repository, version);
    if (existing !== null) {
      response.status(OK_STATUS).json(publicLink(existing));
      return;
    }
    const state = await startIndexing(repository, version);
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
    const updated = await saveState(request, await startIndexing(link.repository, link.version));
    const status = updated.status === LinkStatus.READY ? OK_STATUS : ACCEPTED_STATUS;
    response.status(status).json(publicLink(updated));
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
  if (body === null || typeof body !== "object" || Array.isArray(body)) {
    throw new ApiError(BAD_REQUEST_STATUS, "The body must be a JSON object");
  }
  const unknown = Object.keys(body)
    .filter((fieldName) => !REQUEST_FIELDS.includes(fieldName))
    .sort();
  if (unknown.length > 0) {
    throw new ApiError(BAD_REQUEST_STATUS, `Unknown fields: ${unknown.join(", ")}`);
  }
  const missing = REQUEST_FIELDS.filter((fieldName) => !(fieldName in body));
  if (missing.length > 0) {
    throw new ApiError(BAD_REQUEST_STATUS, `Missing fields: ${missing.join(", ")}`);
  }
  const { repository, version } = body;
  if (!isTextUpTo(repository, MAX_REPOSITORY_LENGTH)) {
    throw new ApiError(BAD_REQUEST_STATUS, REPOSITORY_RULE);
  }
  if (!isTextUpTo(version, MAX_VERSION_LENGTH)) {
    throw new ApiError(BAD_REQUEST_STATUS, VERSION_RULE);
  }
  return { repository: repository.toLowerCase(), version };
}

/**
 * @param {unknown} value
 * @param {number} maxLength
 */
function isTextUpTo(value, maxLength) {
  return typeof value === "string" && value.length > 0 && value.length <= maxLength;
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
  NOTHING_TO_RESUME_MESSAGE,
  PERMANENT_FAILURE_MESSAGE,
  REPOSITORY_RULE,
  TOO_EARLY_MESSAGE,
  UNKNOWN_LINK_MESSAGE,
  VERSION_RULE,
  createRepositoryRouter,
};
