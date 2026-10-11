const request = require("supertest");

const {
  conversationFullMessage,
  NOT_LINKED_MESSAGE,
  NOT_READY_MESSAGE,
  tooManyConversationsMessage,
  UNKNOWN_CONVERSATION_MESSAGE,
  UNKNOWN_LINK_MESSAGE,
  latestKnownCommit,
  titleFor,
} = require("../src/conversationRoutes");
const {
  NOT_SEARCHABLE_MESSAGE,
  REJECTED_ANSWER_MESSAGE,
  RetrievalServiceError,
} = require("../src/retrievalService");
const { HUBOT, OCTOCAT } = require("./support/userStoreContract");
const { TEST_APP_ORIGIN, buildTestApp } = require("./support/testApp");

const QUESTION = "How does Flask sign the session cookie?";

let built;
let app;
let service;
let cookie;
let hubotCookie;

async function setUp(options) {
  built = buildTestApp(options);
  app = built.app;
  service = built.retrievalService;
  cookie = await built.sessionCookieFor(OCTOCAT);
  hubotCookie = await built.sessionCookieFor(HUBOT);
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

async function readyLink(as = cookie, version = "3.1.3") {
  service.markIndexed("pallets/flask", version);
  const response = await send("post", "/api/repositories", { repository: "pallets/flask", version }, as);
  return response.body;
}

async function startConversation(as = cookie) {
  const link = await readyLink(as);
  const response = await send("post", "/api/conversations", { link_id: link.id }, as);
  return { link, conversation: response.body };
}

function ask(conversationId, body = { question: QUESTION }, as = cookie) {
  return send("post", `/api/conversations/${conversationId}/messages`, body, as);
}

describe("starting a conversation", () => {
  test("a ready repository starts an untitled conversation about its version", async () => {
    const link = await readyLink();

    const response = await send("post", "/api/conversations", { link_id: link.id });

    expect(response.status).toBe(201);
    expect(response.body).toEqual({
      id: expect.stringMatching(/^[0-9a-f]{24}$/),
      repository: "pallets/flask",
      version: "3.1.3",
      title: null,
      created_at: "2026-10-07T12:00:00.000Z",
      updated_at: "2026-10-07T12:00:00.000Z",
    });
  });

  test("a repository still indexing is a 409", async () => {
    const indexing = await send("post", "/api/repositories", {
      repository: "pallets/click",
      version: "8.2.1",
    });

    const response = await send("post", "/api/conversations", { link_id: indexing.body.id });

    expect(response.status).toBe(409);
    expect(response.body).toEqual({ error: NOT_READY_MESSAGE });
  });

  test.each([
    ["another person's link", "theirs"],
    ["an unknown link", "0123456789abcdef01234567"],
    ["a link ID that is not text", 7],
  ])("%s is a 404", async (description, linkChoice) => {
    const theirs = await readyLink(hubotCookie);
    const linkId = linkChoice === "theirs" ? theirs.id : linkChoice;

    const response = await send("post", "/api/conversations", { link_id: linkId });

    expect(response.status).toBe(404);
    expect(response.body).toEqual({ error: UNKNOWN_LINK_MESSAGE });
  });

  test("a body with other fields is a 400", async () => {
    const link = await readyLink();

    const response = await send("post", "/api/conversations", { link_id: link.id, title: "x" });

    expect(response.status).toBe(400);
    expect(response.body).toEqual({ error: "Unknown fields: title" });
  });

  test("past the conversation limit it is a 409 and nothing is deleted", async () => {
    await setUp({ limits: { conversationsPerUser: 2 } });
    const link = await readyLink();
    await send("post", "/api/conversations", { link_id: link.id });
    await send("post", "/api/conversations", { link_id: link.id });

    const response = await send("post", "/api/conversations", { link_id: link.id });

    expect(response.status).toBe(409);
    expect(response.body).toEqual({ error: tooManyConversationsMessage(2) });
    expect((await send("get", "/api/conversations")).body.conversations).toHaveLength(2);
  });
});

describe("asking a question", () => {
  test("the whole reply is saved and returned, and the question becomes the title", async () => {
    const { conversation } = await startConversation();
    built.clock.advanceSeconds(30);

    const response = await ask(conversation.id);

    expect(response.status).toBe(201);
    expect(response.body).toEqual({
      id: expect.stringMatching(/^[0-9a-f]{24}$/),
      question: QUESTION,
      exclude_tests: false,
      reply: expect.objectContaining({ found_answer: true }),
      created_at: "2026-10-07T12:00:30.000Z",
    });
    expect(response.body.reply.repository.license.link).toContain("LICENSE.txt");
    expect(service.askCalls).toEqual([
      { repository: "pallets/flask", version: "3.1.3", question: QUESTION, excludeTests: false },
    ]);
    const reopened = await send("get", `/api/conversations/${conversation.id}`);
    expect(reopened.body).toMatchObject({
      title: QUESTION,
      updated_at: "2026-10-07T12:00:30.000Z",
      messages: [response.body],
    });
  });

  test("the question is trimmed and exclude_tests passed on", async () => {
    const { conversation } = await startConversation();

    const response = await ask(conversation.id, { question: `  ${QUESTION}\n`, exclude_tests: true });

    expect(response.body).toMatchObject({ question: QUESTION, exclude_tests: true });
    expect(service.askCalls[0]).toMatchObject({ question: QUESTION, excludeTests: true });
  });

  test("later questions are kept in order and the first question stays the title", async () => {
    const { conversation } = await startConversation();
    await ask(conversation.id);
    built.clock.advanceSeconds(10);
    await ask(conversation.id, { question: "Where is the serializer made?" });

    const reopened = await send("get", `/api/conversations/${conversation.id}`);

    expect(reopened.body.title).toBe(QUESTION);
    expect(reopened.body.messages.map((message) => message.question)).toEqual([
      QUESTION,
      "Where is the serializer made?",
    ]);
  });

  test.each([
    ["no question", {}, "Missing fields: question"],
    ["a blank question", { question: "   " }, "question must be a non-blank string"],
    ["a question over 1,000 characters", { question: "q".repeat(1001) }, "question must be at most 1000 characters"],
    ["exclude_tests as text", { question: QUESTION, exclude_tests: "yes" }, "exclude_tests must be true or false"],
    ["an unknown field", { question: QUESTION, search: "hybrid" }, "Unknown fields: search"],
  ])("%s is a 400 and asks nothing", async (description, body, message) => {
    const { conversation } = await startConversation();

    const response = await ask(conversation.id, body);

    expect(response.status).toBe(400);
    expect(response.body).toEqual({ error: message });
    expect(service.askCalls).toHaveLength(0);
  });

  test("after the repository is removed the history stays but asking is a 409", async () => {
    const { link, conversation } = await startConversation();
    await ask(conversation.id);
    await send("delete", `/api/repositories/${link.id}`);

    const response = await ask(conversation.id);

    expect(response.status).toBe(409);
    expect(response.body).toEqual({ error: NOT_LINKED_MESSAGE });
    expect((await send("get", `/api/conversations/${conversation.id}`)).body.messages).toHaveLength(1);
  });

  test("a full conversation is a 409 and asks nothing", async () => {
    await setUp({ limits: { messagesPerConversation: 2 } });
    const { conversation } = await startConversation();
    await ask(conversation.id);
    await ask(conversation.id);

    const response = await ask(conversation.id);

    expect(response.status).toBe(409);
    expect(response.body).toEqual({ error: conversationFullMessage(2) });
    expect(service.askCalls).toHaveLength(2);
  });

  test.each([
    ["a used-up quota", new RetrievalServiceError(503, "The daily Gemini quota is used up", 3600), 503, "3600"],
    ["an answer that failed its citation check", new RetrievalServiceError(502, REJECTED_ANSWER_MESSAGE), 502, undefined],
    ["a version that cannot be searched", new RetrievalServiceError(409, NOT_SEARCHABLE_MESSAGE), 409, undefined],
  ])("%s passes through and saves nothing", async (description, error, status, retryAfter) => {
    const { conversation } = await startConversation();
    service.failNextAsk(error);

    const response = await ask(conversation.id);

    expect(response.status).toBe(status);
    expect(response.body).toEqual({ error: error.message });
    expect(response.headers["retry-after"]).toBe(retryAfter);
    const reopened = await send("get", `/api/conversations/${conversation.id}`);
    expect(reopened.body).toMatchObject({ title: null, messages: [] });
  });

  test("from another site it is a 403", async () => {
    const { conversation } = await startConversation();

    const response = await request(app)
      .post(`/api/conversations/${conversation.id}/messages`)
      .set("Cookie", cookie)
      .send({ question: QUESTION });

    expect(response.status).toBe(403);
    expect(service.askCalls).toHaveLength(0);
  });
});

describe("reading and deleting history", () => {
  test("the list holds only the person's conversations, most recently used first", async () => {
    const link = await readyLink();
    const older = (await send("post", "/api/conversations", { link_id: link.id })).body;
    built.clock.advanceSeconds(10);
    const newer = (await send("post", "/api/conversations", { link_id: link.id })).body;
    await startConversation(hubotCookie);
    built.clock.advanceSeconds(10);
    await ask(older.id);

    const response = await send("get", "/api/conversations");

    expect(response.body.conversations.map((item) => item.id)).toEqual([older.id, newer.id]);
  });

  test.each([
    ["another person's conversation", "theirs"],
    ["an unknown ID", "0123456789abcdef01234567"],
    ["a malformed ID", "not-an-id"],
  ])("%s is the same 404 everywhere", async (description, choice) => {
    const { conversation: theirs } = await startConversation(hubotCookie);
    await ask(theirs.id, { question: QUESTION }, hubotCookie);
    const conversationId = choice === "theirs" ? theirs.id : choice;

    for (const response of [
      await send("get", `/api/conversations/${conversationId}`),
      await ask(conversationId),
      await send("delete", `/api/conversations/${conversationId}`),
    ]) {
      expect(response.status).toBe(404);
      expect(response.body).toEqual({ error: UNKNOWN_CONVERSATION_MESSAGE });
    }
    const theirsAfter = await send("get", `/api/conversations/${theirs.id}`, undefined, hubotCookie);
    expect(theirsAfter.body.messages).toHaveLength(1);
  });

  test("deleting a conversation removes it and its messages", async () => {
    const { conversation } = await startConversation();
    await ask(conversation.id);

    const response = await send("delete", `/api/conversations/${conversation.id}`);

    expect(response.status).toBe(204);
    expect((await send("get", `/api/conversations/${conversation.id}`)).status).toBe(404);
    expect((await send("get", "/api/conversations")).body.conversations).toEqual([]);
    expect(await built.conversations.countMessages(conversation.id)).toBe(0);
  });

  test("every route needs a session", async () => {
    for (const [method, path] of [
      ["get", "/api/conversations"],
      ["post", "/api/conversations"],
      ["get", "/api/conversations/0123456789abcdef01234567"],
      ["post", "/api/conversations/0123456789abcdef01234567/messages"],
      ["delete", "/api/conversations/0123456789abcdef01234567"],
    ]) {
      const response = await request(app)[method](path).set("Origin", TEST_APP_ORIGIN);
      expect(response.status).toBe(401);
    }
  });
});

describe("titles", () => {
  test("a short question is its own title", () => {
    expect(titleFor("Why?")).toBe("Why?");
  });

  test("a long question is cut to 80 characters ending with an ellipsis", () => {
    const title = titleFor(`${"word ".repeat(30)}end`);

    expect(Array.from(title)).toHaveLength(80);
    expect(title.endsWith("word…")).toBe(true);
  });

  test("an emoji counts as one character and is never split", () => {
    const title = titleFor("🙂".repeat(100));

    expect(Array.from(title)).toEqual([...Array(79).fill("🙂"), "…"]);
  });
});


describe("answers from an older commit", () => {
  const OLD_COMMIT = "c".repeat(40);

  const NEW_COMMIT = "d".repeat(40);

  function readConversation(conversationId) {
    return send("get", `/api/conversations/${conversationId}`);
  }

  test("a conversation names the commit its version is indexed at, from the link", async () => {
    const { conversation } = await startConversation();

    const response = await readConversation(conversation.id);

    expect(response.body.current_commit).toBe(OLD_COMMIT);
  });

  test("an answer from a newer commit moves the link and the conversation's current commit", async () => {
    const { link, conversation } = await startConversation();
    await ask(conversation.id);
    service.setIndexedCommit("pallets/flask", "3.1.3", NEW_COMMIT);
    built.clock.advanceSeconds(60);

    await ask(conversation.id, { question: "And the secret key?" });
    const read = await readConversation(conversation.id);
    const linkNow = await send("get", `/api/repositories/${link.id}`);

    expect(read.body.messages.map((message) => message.reply.repository.commit_id)).toEqual([
      OLD_COMMIT,
      NEW_COMMIT,
    ]);
    expect(read.body.current_commit).toBe(NEW_COMMIT);
    expect(linkNow.body.indexed.commit_id).toBe(NEW_COMMIT);
    expect(linkNow.body.indexed.license.link).toContain(NEW_COMMIT);
  });

  test("a link whose refresh is running is left for the refresh to update", async () => {
    const { link, conversation } = await startConversation();
    service.moveVersion("pallets/flask", "3.1.3", NEW_COMMIT);
    await send("post", `/api/repositories/${link.id}/refresh`);
    service.setIndexedCommit("pallets/flask", "3.1.3", "e".repeat(40));

    await ask(conversation.id);
    const linkNow = await send("get", `/api/repositories/${link.id}`);

    expect(linkNow.body.indexed.commit_id).toBe(OLD_COMMIT);
    expect(linkNow.body.refresh.status).toBe("running");
  });
});

describe("the latest known commit", () => {
  const EARLY = new Date("2026-10-07T12:00:00Z");

  const LATE = new Date("2026-10-07T13:00:00Z");

  function linkAt(commitId, updatedAt) {
    return { indexed: { commit_id: commitId }, updatedAt };
  }

  function messageAt(commitId, createdAt) {
    return { reply: { repository: { commit_id: commitId } }, createdAt };
  }

  test("is whichever was seen last: the link or the newest answer", () => {
    expect(latestKnownCommit(linkAt("a", LATE), [messageAt("b", EARLY)])).toBe("a");
    expect(latestKnownCommit(linkAt("a", EARLY), [messageAt("x", EARLY), messageAt("b", LATE)])).toBe(
      "b",
    );
  });

  test("uses the answers alone once the repository is removed", () => {
    expect(latestKnownCommit(null, [messageAt("b", EARLY)])).toBe("b");
  });

  test("is unknown with no link and no answers", () => {
    expect(latestKnownCommit(null, [])).toBeNull();
    expect(latestKnownCommit({ indexed: null, updatedAt: LATE }, [])).toBeNull();
  });
});
