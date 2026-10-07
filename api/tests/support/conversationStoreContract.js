const FIRST_USER = "0123456789abcdef01234567";

const SECOND_USER = "fedcba9876543210fedcba98";

const STARTED = new Date("2026-10-07T12:00:00Z");

const ASKED = new Date("2026-10-07T12:01:00Z");

const ASKED_LATER = new Date("2026-10-07T12:02:00Z");

const REPLY = {
  found_answer: true,
  answer: "It signs the cookie [1, 303-305].",
  sentences: [{ text: "It signs the cookie.", citations: [{ source: 1, start_line: 303, end_line: 305 }] }],
  sources: [],
  snippets: [],
};

/**
 * The behaviour every conversation store must have. `setUp` runs before each test and returns an
 * empty store.
 * @param {() => Promise<any>} setUp
 */
function describeConversationStoreContract(setUp) {
  let store;

  beforeEach(async () => {
    store = await setUp();
  });

  function start(overrides = {}) {
    return store.create({
      userId: FIRST_USER,
      repository: "pallets/flask",
      version: "3.1.3",
      now: STARTED,
      ...overrides,
    });
  }

  function ask(conversation, overrides = {}) {
    return store.addMessage({
      userId: conversation.userId,
      conversationId: conversation.id,
      question: "How is the session cookie signed?",
      excludeTests: false,
      reply: REPLY,
      title: "How is the session cookie signed?",
      now: ASKED,
      ...overrides,
    });
  }

  test("a new conversation has no title and no messages", async () => {
    const conversation = await start();

    expect(conversation).toEqual({
      id: expect.stringMatching(/^[0-9a-f]{24}$/),
      userId: FIRST_USER,
      repository: "pallets/flask",
      version: "3.1.3",
      title: null,
      createdAt: STARTED,
      updatedAt: STARTED,
    });
    expect(await store.findForUser(FIRST_USER, conversation.id)).toEqual(conversation);
    expect(await store.countMessages(conversation.id)).toBe(0);
    expect(await store.listMessages(conversation.id)).toEqual([]);
  });

  test("a message keeps the question and the whole reply, and marks the conversation used", async () => {
    const conversation = await start();

    const message = await ask(conversation);

    expect(message).toEqual({
      id: expect.stringMatching(/^[0-9a-f]{24}$/),
      conversationId: conversation.id,
      question: "How is the session cookie signed?",
      excludeTests: false,
      reply: REPLY,
      createdAt: ASKED,
    });
    expect(await store.listMessages(conversation.id)).toEqual([message]);
    expect(await store.findForUser(FIRST_USER, conversation.id)).toMatchObject({
      title: "How is the session cookie signed?",
      updatedAt: ASKED,
    });
  });

  test("the first question's title stays; messages list oldest first", async () => {
    const conversation = await start();
    const first = await ask(conversation);
    const second = await ask(conversation, {
      question: "What signs it?",
      title: "What signs it?",
      excludeTests: true,
      now: ASKED_LATER,
    });

    expect((await store.listMessages(conversation.id)).map((message) => message.id)).toEqual([
      first.id,
      second.id,
    ]);
    expect(await store.countMessages(conversation.id)).toBe(2);
    expect(await store.findForUser(FIRST_USER, conversation.id)).toMatchObject({
      title: "How is the session cookie signed?",
      updatedAt: ASKED_LATER,
    });
  });

  test("a person's list holds only their conversations, most recently used first", async () => {
    const older = await start();
    const newer = await start({ version: "3.1.2" });
    await start({ userId: SECOND_USER });
    await ask(older, { now: ASKED_LATER });

    const listed = await store.listForUser(FIRST_USER);

    expect(listed.map((item) => item.id)).toEqual([older.id, newer.id]);
    expect(await store.countForUser(FIRST_USER)).toBe(2);
    expect(await store.countForUser(SECOND_USER)).toBe(1);
  });

  test("a person cannot find or delete another person's conversation", async () => {
    const theirs = await start({ userId: SECOND_USER });
    await ask(theirs);

    expect(await store.findForUser(FIRST_USER, theirs.id)).toBeNull();
    expect(await store.remove(FIRST_USER, theirs.id)).toBe(false);
    expect(await store.countMessages(theirs.id)).toBe(1);
  });

  test.each(["0123456789abcdef01234567", "not-an-id", ""])(
    "conversation ID %p that does not exist is found nowhere",
    async (conversationId) => {
      expect(await store.findForUser(FIRST_USER, conversationId)).toBeNull();
      expect(await store.remove(FIRST_USER, conversationId)).toBe(false);
    },
  );

  test("deleting a conversation deletes its messages and no others", async () => {
    const removed = await start();
    const kept = await start({ version: "3.1.2" });
    await ask(removed);
    await ask(kept);

    expect(await store.remove(FIRST_USER, removed.id)).toBe(true);

    expect(await store.findForUser(FIRST_USER, removed.id)).toBeNull();
    expect(await store.countMessages(removed.id)).toBe(0);
    expect(await store.countMessages(kept.id)).toBe(1);
  });
}

module.exports = { FIRST_USER, REPLY, describeConversationStoreContract };
