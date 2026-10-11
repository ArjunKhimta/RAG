const request = require("supertest");

const {
  NOT_READY_MESSAGE,
  NOTHING_TO_RESUME_MESSAGE,
  PERMANENT_FAILURE_MESSAGE,
  REPOSITORY_RULE,
  TOO_EARLY_MESSAGE,
  UNKNOWN_LINK_MESSAGE,
  VERSION_RULE,
} = require("../src/repositoryRoutes");
const { RetrievalServiceError, UNEXPECTED_REPLY_MESSAGE } = require("../src/retrievalService");
const { repositoryBlock } = require("./support/fakeRetrievalService");
const { HUBOT, OCTOCAT } = require("./support/userStoreContract");
const { TEST_APP_ORIGIN, buildTestApp } = require("./support/testApp");

let app;
let clock;
let service;
let cookie;
let hubotCookie;

beforeEach(async () => {
  const built = buildTestApp();
  ({ app, clock } = built);
  service = built.retrievalService;
  cookie = await built.sessionCookieFor(OCTOCAT);
  hubotCookie = await built.sessionCookieFor(HUBOT);
});

function addLink(body, as = cookie) {
  return request(app)
    .post("/api/repositories")
    .set("Origin", TEST_APP_ORIGIN)
    .set("Cookie", as)
    .send(body);
}

function readLink(linkId, as = cookie) {
  return request(app).get(`/api/repositories/${linkId}`).set("Cookie", as);
}

function resume(linkId, as = cookie) {
  return request(app)
    .post(`/api/repositories/${linkId}/resume`)
    .set("Origin", TEST_APP_ORIGIN)
    .set("Cookie", as);
}

function removeLink(linkId, as = cookie) {
  return request(app)
    .delete(`/api/repositories/${linkId}`)
    .set("Origin", TEST_APP_ORIGIN)
    .set("Cookie", as);
}

function listLinks(as = cookie) {
  return request(app).get("/api/repositories").set("Cookie", as);
}

const FLASK = { repository: "pallets/flask", version: "3.1.3" };

async function indexingLink() {
  const response = await addLink(FLASK);
  return { link: response.body, job: service.jobFor(FLASK.repository, FLASK.version) };
}

describe("every route needs a session", () => {
  test.each([
    ["GET", "/api/repositories"],
    ["POST", "/api/repositories"],
    ["GET", "/api/repositories/0123456789abcdef01234567"],
    ["POST", "/api/repositories/0123456789abcdef01234567/resume"],
    ["DELETE", "/api/repositories/0123456789abcdef01234567"],
  ])("%s %s without one is a 401", async (method, path) => {
    const response = await request(app)[method.toLowerCase()](path).set("Origin", TEST_APP_ORIGIN);

    expect(response.status).toBe(401);
    expect(service.startCalls).toHaveLength(0);
  });

  test("adding a link from another site is a 403", async () => {
    const response = await request(app)
      .post("/api/repositories")
      .set("Origin", "https://attacker.example")
      .set("Cookie", cookie)
      .send(FLASK);

    expect(response.status).toBe(403);
    expect(service.startCalls).toHaveLength(0);
  });
});

describe("adding a repository", () => {
  test("a version already indexed is ready at once, with its commit and license", async () => {
    service.markIndexed("pallets/flask", "3.1.3");

    const response = await addLink(FLASK);

    expect(response.status).toBe(201);
    const block = repositoryBlock("pallets/flask", "3.1.3");
    expect(response.body).toEqual({
      id: expect.stringMatching(/^[0-9a-f]{24}$/),
      repository: "pallets/flask",
      version: "3.1.3",
      status: "ready",
      stage: null,
      progress: null,
      error: null,
      resume_after: null,
      indexed: { commit_id: block.commit_id, license: block.license },
      created_at: "2026-10-07T12:00:00.000Z",
      updated_at: "2026-10-07T12:00:00.000Z",
    });
  });

  test("a new version starts indexing; the reply shows no job ID", async () => {
    const response = await addLink(FLASK);

    expect(response.status).toBe(202);
    expect(response.body.status).toBe("indexing");
    expect(response.body).not.toHaveProperty("job_id");
    expect(response.body).not.toHaveProperty("jobId");
  });

  test("the repository name is lowercased before the service sees it; the version is kept", async () => {
    const response = await addLink({ repository: "Pallets/Flask", version: "Main-Branch" });

    expect(service.startCalls).toEqual([{ repository: "pallets/flask", version: "Main-Branch" }]);
    expect(response.body.repository).toBe("pallets/flask");
    expect(response.body.version).toBe("Main-Branch");
  });

  test("adding it again returns the same link without asking the service", async () => {
    const first = await addLink(FLASK);

    const second = await addLink({ ...FLASK, repository: "PALLETS/flask" });

    expect(second.status).toBe(200);
    expect(second.body.id).toBe(first.body.id);
    expect(service.startCalls).toHaveLength(1);
  });

  test("two people adding the same version share one indexing job", async () => {
    await addLink(FLASK);
    await addLink(FLASK, hubotCookie);

    const job = service.jobFor("pallets/flask", "3.1.3");
    service.finishJob(job.job_id);
    const mine = (await listLinks()).body.repositories[0];
    const theirs = (await listLinks(hubotCookie)).body.repositories[0];

    expect(mine.id).not.toBe(theirs.id);
    expect((await readLink(mine.id)).body.status).toBe("ready");
    expect((await readLink(theirs.id, hubotCookie)).body.status).toBe("ready");
  });

  test.each([
    ["a list", [], "The body must be a JSON object"],
    ["an unknown field", { ...FLASK, exclude_tests: true }, "Unknown fields: exclude_tests"],
    ["no version", { repository: "pallets/flask" }, "Missing fields: version"],
    ["a repository that is not text", { ...FLASK, repository: 7 }, REPOSITORY_RULE],
    ["an empty repository", { ...FLASK, repository: "" }, REPOSITORY_RULE],
    ["a version over 100 characters", { ...FLASK, version: "v".repeat(101) }, VERSION_RULE],
  ])("%s is a 400 and saves nothing", async (description, body, message) => {
    const response = await addLink(body);

    expect(response.status).toBe(400);
    expect(response.body).toEqual({ error: message });
    expect(service.startCalls).toHaveLength(0);
    expect((await listLinks()).body.repositories).toEqual([]);
  });

  test("the service's 400 passes through and saves nothing", async () => {
    service.failNextStart(new RetrievalServiceError(400, REPOSITORY_RULE));

    const response = await addLink({ repository: "not a name", version: "1" });

    expect(response.status).toBe(400);
    expect(response.body).toEqual({ error: REPOSITORY_RULE });
    expect((await listLinks()).body.repositories).toEqual([]);
  });

  test("a busy service is a 503 with its Retry-After, and saves nothing", async () => {
    service.failNextStart(
      new RetrievalServiceError(503, "Another repository is being indexed; try again later", 60),
    );

    const response = await addLink(FLASK);

    expect(response.status).toBe(503);
    expect(response.headers["retry-after"]).toBe("60");
    expect((await listLinks()).body.repositories).toEqual([]);
  });
});

describe("listing and reading repositories", () => {
  test("the list holds only the person's links, newest first, and never calls the service", async () => {
    service.markIndexed("pallets/flask", "3.1.2");
    const older = await addLink({ ...FLASK, version: "3.1.2" });
    clock.advanceSeconds(60);
    const newer = await addLink(FLASK);
    await addLink({ repository: "pallets/click", version: "8.2.1" }, hubotCookie);
    const jobCallsBefore = service.jobCalls.length;

    const response = await listLinks();

    expect(response.status).toBe(200);
    expect(response.body.repositories.map((link) => link.id)).toEqual([
      newer.body.id,
      older.body.id,
    ]);
    expect(service.jobCalls).toHaveLength(jobCallsBefore);
  });

  test("reading an indexing link fetches and saves the job's stage and progress", async () => {
    const { link, job } = await indexingLink();
    service.updateJob(job.job_id, { stage: "embedding", progress: { embedded: 80, to_embed: 158 } });
    clock.advanceSeconds(5);

    const response = await readLink(link.id);

    expect(response.body).toMatchObject({
      status: "indexing",
      stage: "embedding",
      progress: { embedded: 80, to_embed: 158 },
      updated_at: "2026-10-07T12:00:05.000Z",
    });
    expect((await listLinks()).body.repositories[0].progress).toEqual({
      embedded: 80,
      to_embed: 158,
    });
  });

  test("when the job succeeds, the link becomes ready with the license from the service", async () => {
    const { link, job } = await indexingLink();
    service.finishJob(job.job_id);

    const response = await readLink(link.id);

    expect(response.body).toMatchObject({ status: "ready", stage: null, progress: null });
    expect(response.body.indexed.license.link).toContain("/LICENSE.txt");
    expect(service.startCalls).toHaveLength(2);
  });

  test("a ready link is read without calling the service", async () => {
    service.markIndexed("pallets/flask", "3.1.3");
    const { body } = await addLink(FLASK);

    await readLink(body.id);

    expect(service.jobCalls).toHaveLength(0);
  });

  test("a job waiting for quota shows when indexing may resume", async () => {
    const { link, job } = await indexingLink();
    service.updateJob(job.job_id, {
      status: "waiting_for_quota",
      error: { code: "waiting_for_quota", message: "The daily embedding quota is used up" },
      resume_after: "2026-10-08T07:00:00Z",
    });

    const response = await readLink(link.id);

    expect(response.body).toMatchObject({
      status: "waiting_for_quota",
      stage: null,
      resume_after: "2026-10-08T07:00:00.000Z",
      error: { code: "waiting_for_quota", message: "The daily embedding quota is used up" },
    });
  });

  test("a failed job shows its code and message", async () => {
    const { link, job } = await indexingLink();
    const error = { code: "not_found", message: "No such public repository on GitHub" };
    service.updateJob(job.job_id, { status: "failed", error });

    const response = await readLink(link.id);

    expect(response.body).toMatchObject({ status: "failed", error });
  });

  test("a job the service no longer knows leaves the link interrupted", async () => {
    const { link, job } = await indexingLink();
    service.forgetJob(job.job_id);

    const response = await readLink(link.id);

    expect(response.body.status).toBe("interrupted");
  });

  test("a job status the service never sends is a 502 and changes nothing", async () => {
    const { link, job } = await indexingLink();
    service.updateJob(job.job_id, { status: "exploded" });

    const response = await readLink(link.id);

    expect(response.status).toBe(502);
    expect(response.body).toEqual({ error: UNEXPECTED_REPLY_MESSAGE });
    expect((await listLinks()).body.repositories[0].status).toBe("indexing");
  });

  test("a service out of reach is a 503 and changes nothing", async () => {
    const { link } = await indexingLink();
    service.failNextJob(new RetrievalServiceError(503, "unavailable", 30));

    const response = await readLink(link.id);

    expect(response.status).toBe(503);
    expect(response.headers["retry-after"]).toBe("30");
    expect((await listLinks()).body.repositories[0].status).toBe("indexing");
  });

  test.each([
    ["another person's link", "theirs"],
    ["an unknown ID", "0123456789abcdef01234567"],
    ["a malformed ID", "not-an-id"],
  ])("%s is the same 404", async (description, linkChoice) => {
    const theirs = await addLink(FLASK, hubotCookie);
    const linkId = linkChoice === "theirs" ? theirs.body.id : linkChoice;

    for (const response of [await readLink(linkId), await resume(linkId), await removeLink(linkId)]) {
      expect(response.status).toBe(404);
      expect(response.body).toEqual({ error: UNKNOWN_LINK_MESSAGE });
    }
    expect((await listLinks(hubotCookie)).body.repositories).toHaveLength(1);
  });
});

describe("resuming indexing", () => {
  async function linkInState(changes) {
    const { link, job } = await indexingLink();
    service.updateJob(job.job_id, changes);
    await readLink(link.id);
    return { link, job };
  }

  test("before the quota resets it is a 409 saying how long to wait", async () => {
    const { link } = await linkInState({
      status: "waiting_for_quota",
      resume_after: "2026-10-07T12:10:00Z",
    });

    const response = await resume(link.id);

    expect(response.status).toBe(409);
    expect(response.body).toEqual({ error: TOO_EARLY_MESSAGE });
    expect(response.headers["retry-after"]).toBe("600");
  });

  test("after the quota resets it starts a new job and shows indexing", async () => {
    const { link } = await linkInState({
      status: "waiting_for_quota",
      resume_after: "2026-10-07T12:10:00Z",
    });
    clock.advanceSeconds(600);

    const response = await resume(link.id);

    expect(response.status).toBe(202);
    expect(response.body).toMatchObject({ status: "indexing", resume_after: null, error: null });
    expect(service.startCalls).toHaveLength(2);
  });

  test("an interrupted link resumes", async () => {
    const { link, job } = await indexingLink();
    service.forgetJob(job.job_id);
    await readLink(link.id);

    const response = await resume(link.id);

    expect(response.status).toBe(202);
    expect(response.body.status).toBe("indexing");
  });

  test("a temporary failure may be tried again", async () => {
    const { link } = await linkInState({
      status: "failed",
      error: { code: "clone_failed", message: "Cloning failed" },
    });

    expect((await resume(link.id)).status).toBe(202);
  });

  test("a failure that will recur is a 409 and calls nothing", async () => {
    const { link } = await linkInState({
      status: "failed",
      error: { code: "license_not_allowed", message: "The license is not allowed" },
    });

    const response = await resume(link.id);

    expect(response.status).toBe(409);
    expect(response.body).toEqual({ error: PERMANENT_FAILURE_MESSAGE });
    expect(service.startCalls).toHaveLength(1);
  });

  test.each([
    ["indexing", false],
    ["ready", true],
  ])("a link that is %s has nothing to resume", async (status, indexedAlready) => {
    if (indexedAlready) {
      service.markIndexed("pallets/flask", "3.1.3");
    }
    const { body } = await addLink(FLASK);

    const response = await resume(body.id);

    expect(response.status).toBe(409);
    expect(response.body).toEqual({ error: NOTHING_TO_RESUME_MESSAGE });
  });

  test("a version indexed meanwhile resumes straight to ready", async () => {
    const { link, job } = await linkInState({ status: "interrupted" });
    service.markIndexed(job.repository, job.version);

    const response = await resume(link.id);

    expect(response.status).toBe(200);
    expect(response.body.status).toBe("ready");
  });
});

describe("searching a repository", () => {
  function search(linkId, body, as = cookie) {
    return request(app)
      .post(`/api/repositories/${linkId}/search`)
      .set("Origin", TEST_APP_ORIGIN)
      .set("Cookie", as)
      .send(body);
  }

  async function readyFlask(as = cookie) {
    service.markIndexed("pallets/flask", "3.1.3");
    return (await addLink(FLASK, as)).body;
  }

  test("a ready link is searched with the trimmed query and the options given", async () => {
    const link = await readyFlask();

    const response = await search(link.id, { query: "  url_for  ", limit: 3, exclude_tests: true });

    expect(response.status).toBe(200);
    expect(response.body.results[0].qualified_name).toBe("url_for");
    expect(response.body.repository.license.link).toContain("LICENSE.txt");
    expect(service.searchCalls).toEqual([
      {
        repository: "pallets/flask",
        version: "3.1.3",
        query: "url_for",
        limit: 3,
        excludeTests: true,
      },
    ]);
  });

  test("options left out are not sent", async () => {
    const link = await readyFlask();

    await search(link.id, { query: "url_for" });

    expect(service.searchCalls[0]).toEqual({
      repository: "pallets/flask",
      version: "3.1.3",
      query: "url_for",
    });
  });

  test("a link still indexing is a 409", async () => {
    const { link } = await indexingLink();

    const response = await search(link.id, { query: "url_for" });

    expect(response.status).toBe(409);
    expect(response.body).toEqual({ error: NOT_READY_MESSAGE });
    expect(service.searchCalls).toHaveLength(0);
  });

  test("another person's link is a 404", async () => {
    const theirs = await readyFlask(hubotCookie);

    const response = await search(theirs.id, { query: "url_for" });

    expect(response.status).toBe(404);
    expect(service.searchCalls).toHaveLength(0);
  });

  test.each([
    ["a limit of 21", { query: "q", limit: 21 }, "limit must be a whole number from 1 to 20"],
    ["a limit of 2.5", { query: "q", limit: 2.5 }, "limit must be a whole number from 1 to 20"],
    ["a blank query", { query: " " }, "query must be a non-blank string"],
    ["a search mode", { query: "q", mode: "hybrid" }, "Unknown fields: mode"],
  ])("%s is a 400 and searches nothing", async (description, body, message) => {
    const link = await readyFlask();

    const response = await search(link.id, body);

    expect(response.status).toBe(400);
    expect(response.body).toEqual({ error: message });
    expect(service.searchCalls).toHaveLength(0);
  });

  test("searches are not kept in history", async () => {
    const link = await readyFlask();

    await search(link.id, { query: "url_for" });

    const history = await request(app).get("/api/conversations").set("Cookie", cookie);
    expect(history.body.conversations).toEqual([]);
  });
});

describe("a repository's overview", () => {
  function overview(linkId, as = cookie) {
    return request(app).get(`/api/repositories/${linkId}/overview`).set("Cookie", as);
  }

  test("a ready link's overview comes from the service for its version", async () => {
    service.markIndexed("pallets/flask", "3.1.3");
    const link = (await addLink(FLASK)).body;

    const response = await overview(link.id);

    expect(response.status).toBe(200);
    expect(response.body.links[0]).toEqual({
      from: "src/flask/app.py",
      to: "src/flask/helpers.py",
      calls: 9,
    });
    expect(response.body.repository.license.link).toContain("LICENSE.txt");
    expect(service.overviewCalls).toEqual([{ repository: "pallets/flask", version: "3.1.3" }]);
  });

  test("it uses no daily slot", async () => {
    service.markIndexed("pallets/flask", "3.1.3");
    const link = (await addLink(FLASK)).body;

    await overview(link.id);
    await overview(link.id);

    const usage = await request(app).get("/api/me/usage").set("Cookie", cookie);
    expect(usage.body.searches.used).toBe(0);
    expect(usage.body.questions.used).toBe(0);
  });

  test("a link still indexing is a 409 and asks the service nothing", async () => {
    const { link } = await indexingLink();

    const response = await overview(link.id);

    expect(response.status).toBe(409);
    expect(response.body).toEqual({ error: NOT_READY_MESSAGE });
    expect(service.overviewCalls).toHaveLength(0);
  });

  test("another person's link is a 404", async () => {
    service.markIndexed("pallets/flask", "3.1.3");
    const theirs = (await addLink(FLASK, hubotCookie)).body;

    const response = await overview(theirs.id);

    expect(response.status).toBe(404);
    expect(service.overviewCalls).toHaveLength(0);
  });

  test("the service's refusal passes through with its Retry-After", async () => {
    service.markIndexed("pallets/flask", "3.1.3");
    const link = (await addLink(FLASK)).body;
    service.failNextOverview(
      new RetrievalServiceError(503, "The database is unavailable for now; try again shortly", 30),
    );

    const response = await overview(link.id);

    expect(response.status).toBe(503);
    expect(response.headers["retry-after"]).toBe("30");
  });

  test("without a session it is a 401", async () => {
    const response = await request(app).get("/api/repositories/anything/overview");

    expect(response.status).toBe(401);
  });
});

describe("removing a repository", () => {
  test("removes only the person's link", async () => {
    const mine = await addLink(FLASK);
    await addLink(FLASK, hubotCookie);

    const response = await removeLink(mine.body.id);

    expect(response.status).toBe(204);
    expect((await readLink(mine.body.id)).status).toBe(404);
    expect((await listLinks()).body.repositories).toEqual([]);
    expect((await listLinks(hubotCookie)).body.repositories).toHaveLength(1);
  });

  test("from another site it is a 403 and removes nothing", async () => {
    const mine = await addLink(FLASK);

    const response = await request(app)
      .delete(`/api/repositories/${mine.body.id}`)
      .set("Cookie", cookie);

    expect(response.status).toBe(403);
    expect((await listLinks()).body.repositories).toHaveLength(1);
  });
});
