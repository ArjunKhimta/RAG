const request = require("supertest");

const { TOO_MANY_QUESTIONS_THIS_MINUTE_MESSAGE } = require("../src/conversationRoutes");
const { tooManyRepositoriesMessage } = require("../src/repositoryRoutes");
const {
  NOT_SEARCHABLE_MESSAGE,
  REJECTED_ANSWER_MESSAGE,
  RetrievalServiceError,
} = require("../src/retrievalService");
const { UsageKind, dailyLimitMessage } = require("../src/usage");
const { HUBOT, OCTOCAT } = require("./support/userStoreContract");
const { TEST_APP_ORIGIN, buildTestApp } = require("./support/testApp");

const TODAY = "2026-10-07";

const SECONDS_TO_MIDNIGHT_PACIFIC = 19 * 3600;

let built;
let app;
let service;
let cookie;
let userId;

async function setUp(limits) {
  built = buildTestApp({ limits });
  ({ app } = built);
  service = built.retrievalService;
  cookie = await built.sessionCookieFor(OCTOCAT);
  userId = (await built.users.saveSignedInUser(OCTOCAT, new Date(built.clock.now()))).id;
}

beforeEach(async () => {
  await setUp();
});

function send(method, path, body, as = cookie) {
  let attempt = request(app)[method](path).set("Cookie", as).set("Origin", TEST_APP_ORIGIN);
  if (body !== undefined) {
    attempt = attempt.send(body);
  }
  return attempt;
}

function used(kind) {
  return built.usage.read(userId, TODAY).then((counts) => counts[kind]);
}

async function readyConversation() {
  service.markIndexed("pallets/flask", "3.1.3");
  const link = await send("post", "/api/repositories", {
    repository: "pallets/flask",
    version: "3.1.3",
  });
  const conversation = await send("post", "/api/conversations", { link_id: link.body.id });
  return { link: link.body, conversation: conversation.body };
}

function ask(conversationId) {
  return send("post", `/api/conversations/${conversationId}/messages`, { question: "Why?" });
}

describe("questions", () => {
  test("the 31st question of the day is a 429 until midnight Pacific", async () => {
    const { conversation } = await readyConversation();
    built.usage.setCount(userId, TODAY, UsageKind.QUESTIONS, 29);

    const last = await ask(conversation.id);
    const refused = await ask(conversation.id);

    expect(last.status).toBe(201);
    expect(refused.status).toBe(429);
    expect(refused.body).toEqual({ error: dailyLimitMessage(UsageKind.QUESTIONS, 30) });
    expect(refused.headers["retry-after"]).toBe(String(SECONDS_TO_MIDNIGHT_PACIFIC));
    expect(service.askCalls).toHaveLength(1);
  });

  test("the 6th question within a minute is a 429 that does not use a daily slot", async () => {
    const { conversation } = await readyConversation();
    for (let index = 0; index < 5; index += 1) {
      expect((await ask(conversation.id)).status).toBe(201);
    }

    const refused = await ask(conversation.id);

    expect(refused.status).toBe(429);
    expect(refused.body).toEqual({ error: TOO_MANY_QUESTIONS_THIS_MINUTE_MESSAGE });
    expect(refused.headers["retry-after"]).toBe("60");
    expect(await used(UsageKind.QUESTIONS)).toBe(5);

    built.clock.advanceSeconds(60);
    expect((await ask(conversation.id)).status).toBe(201);
  });

  test("a daily refusal gives back the minute's slot", async () => {
    await setUp({ questionsPerDay: 1 });
    const { conversation } = await readyConversation();
    await ask(conversation.id);
    for (let index = 0; index < 6; index += 1) {
      expect((await ask(conversation.id)).status).toBe(429);
    }

    const refusal = await ask(conversation.id);

    expect(refusal.body).toEqual({ error: dailyLimitMessage(UsageKind.QUESTIONS, 1) });
  });

  test.each([
    ["the service unavailable", new RetrievalServiceError(503, "Gemini is busy", 30), 0],
    ["a version that cannot be searched", new RetrievalServiceError(409, NOT_SEARCHABLE_MESSAGE), 0],
    ["a bad request", new RetrievalServiceError(400, "question must be a non-blank string"), 0],
    ["an answer that failed its citation check", new RetrievalServiceError(502, REJECTED_ANSWER_MESSAGE), 1],
  ])("with %s the question counts %i", async (description, error, counted) => {
    const { conversation } = await readyConversation();
    service.failNextAsk(error);

    await ask(conversation.id);

    expect(await used(UsageKind.QUESTIONS)).toBe(counted);
  });

  test("a question refused before asking (bad body, full conversation) uses nothing", async () => {
    const { conversation } = await readyConversation();

    await send("post", `/api/conversations/${conversation.id}/messages`, { question: " " });

    expect(await used(UsageKind.QUESTIONS)).toBe(0);
  });
});

describe("searches", () => {
  function search(linkId) {
    return send("post", `/api/repositories/${linkId}/search`, { query: "url_for" });
  }

  test("the 101st search of the day is a 429", async () => {
    const { link } = await readyConversation();
    built.usage.setCount(userId, TODAY, UsageKind.SEARCHES, 99);

    expect((await search(link.id)).status).toBe(200);
    const refused = await search(link.id);

    expect(refused.status).toBe(429);
    expect(refused.body).toEqual({ error: dailyLimitMessage(UsageKind.SEARCHES, 100) });
    expect(refused.headers["retry-after"]).toBe(String(SECONDS_TO_MIDNIGHT_PACIFIC));
    expect(service.searchCalls).toHaveLength(1);
  });

  test("a search the service refused is given back; a bad body uses nothing", async () => {
    const { link } = await readyConversation();
    service.failNextSearch(new RetrievalServiceError(503, "unavailable", 30));

    await search(link.id);
    await send("post", `/api/repositories/${link.id}/search`, { query: "q", limit: 99 });

    expect(await used(UsageKind.SEARCHES)).toBe(0);
  });
});

describe("indexing", () => {
  function add(repository, version = "1.0") {
    return send("post", "/api/repositories", { repository, version });
  }

  test("two new versions a day start indexing; the third is a 429", async () => {
    expect((await add("pallets/click")).status).toBe(202);
    expect((await add("pallets/jinja")).status).toBe(202);

    const refused = await add("pallets/werkzeug");

    expect(refused.status).toBe(429);
    expect(refused.body).toEqual({ error: dailyLimitMessage(UsageKind.INDEXING_REQUESTS, 2) });
    expect(service.startCalls).toHaveLength(2);
    expect((await send("get", "/api/repositories")).body.repositories).toHaveLength(2);
  });

  test("an already indexed version, an existing link, or a refusal uses no slot", async () => {
    service.markIndexed("pallets/flask", "3.1.3");
    await add("pallets/flask", "3.1.3");
    await add("pallets/flask", "3.1.3");
    service.failNextStart(new RetrievalServiceError(503, "busy", 60));
    await add("pallets/click");
    service.failNextStart(new RetrievalServiceError(400, "repository must be owner/repo, as on GitHub"));
    await add("pallets/bad name");

    expect(await used(UsageKind.INDEXING_REQUESTS)).toBe(0);
  });

  test("resuming uses a slot, and reading a finished job does not", async () => {
    const added = await add("pallets/click");
    const job = service.jobFor("pallets/click", "1.0");
    service.updateJob(job.job_id, { status: "interrupted" });
    await send("get", `/api/repositories/${added.body.id}`);

    await send("post", `/api/repositories/${added.body.id}/resume`);
    service.finishJob(service.jobFor("pallets/click", "1.0").job_id);
    service.finishJob(job.job_id);
    await send("get", `/api/repositories/${added.body.id}`);

    expect(await used(UsageKind.INDEXING_REQUESTS)).toBe(2);
  });

  test("a sixth link is a 409 with no Retry-After, and uses no slot", async () => {
    for (const version of ["1", "2", "3", "4", "5"]) {
      service.markIndexed("pallets/flask", version);
      await add("pallets/flask", version);
    }

    const refused = await add("pallets/click");

    expect(refused.status).toBe(409);
    expect(refused.body).toEqual({ error: tooManyRepositoriesMessage(5) });
    expect(refused.headers["retry-after"]).toBeUndefined();
    expect(service.startCalls).toHaveLength(5);
  });

  test("an existing link is returned even at the cap", async () => {
    for (const version of ["1", "2", "3", "4", "5"]) {
      service.markIndexed("pallets/flask", version);
      await add("pallets/flask", version);
    }

    expect((await add("pallets/flask", "3")).status).toBe(200);
  });
});

describe("sign-in attempts", () => {
  function openLoginLink() {
    return request(app).get("/api/auth/github/login");
  }

  test("the 21st opening of the login link within 15 minutes goes back to the site", async () => {
    for (let index = 0; index < 20; index += 1) {
      expect(new URL((await openLoginLink()).headers.location).hostname).toBe("github.example");
    }

    const refused = await openLoginLink();

    expect(refused.status).toBe(302);
    expect(refused.headers.location).toBe(`${TEST_APP_ORIGIN}/?sign_in=rate_limited`);
    expect(refused.headers["set-cookie"]).toBeUndefined();
    expect(built.github.authorizations).toHaveLength(20);

    built.clock.advanceSeconds(15 * 60);
    expect(new URL((await openLoginLink()).headers.location).hostname).toBe("github.example");
  });
});

describe("GET /api/me/usage", () => {
  test("shows today's use, the limits, linked repositories, and the reset time", async () => {
    const { conversation, link } = await readyConversation();
    await ask(conversation.id);
    await send("post", `/api/repositories/${link.id}/search`, { query: "url_for" });

    const response = await send("get", "/api/me/usage");

    expect(response.status).toBe(200);
    expect(response.body).toEqual({
      day: TODAY,
      resets_at: "2026-10-08T07:00:00.000Z",
      questions: { used: 1, limit: 30 },
      searches: { used: 1, limit: 100 },
      indexing_requests: { used: 0, limit: 2 },
      linked_repositories: { used: 1, limit: 5 },
    });
  });

  test("needs a session", async () => {
    expect((await request(app).get("/api/me/usage")).status).toBe(401);
  });

  test("counts are per person", async () => {
    const { conversation } = await readyConversation();
    await ask(conversation.id);
    const hubotCookie = await built.sessionCookieFor(HUBOT);

    const response = await send("get", "/api/me/usage", undefined, hubotCookie);

    expect(response.body.questions.used).toBe(0);
    expect(response.body.linked_repositories.used).toBe(0);
  });
});
