const { indexingState, readyState } = require("../../src/linkStates");
const { LinkStatus } = require("../../src/repositoryLinks");

const FIRST_USER = "0123456789abcdef01234567";

const SECOND_USER = "fedcba9876543210fedcba98";

const CREATED_AT = new Date("2026-10-07T12:00:00Z");

const LATER = new Date("2026-10-07T12:05:00Z");

const JOB_ID = "0123456789abcdef0123456789abcdef";

const FLASK_BLOCK = {
  commit_id: "22d924701a6ae2e4cd01e9a15bbaf3946094af65",
  license: {
    spdx_id: "BSD-3-Clause",
    name: "BSD 3-Clause",
    path: "LICENSE.txt",
    link: "https://github.com/pallets/flask/blob/22d9247/LICENSE.txt",
  },
};

/**
 * The behaviour every repository link store must have. `setUp` runs before each test and returns
 * an empty store.
 * @param {() => Promise<any>} setUp
 */
function describeRepositoryLinkStoreContract(setUp) {
  let store;

  beforeEach(async () => {
    store = await setUp();
  });

  function link(overrides = {}) {
    return store.create({
      userId: FIRST_USER,
      repository: "pallets/flask",
      version: "3.1.3",
      state: indexingState(JOB_ID),
      now: CREATED_AT,
      ...overrides,
    });
  }

  test("a new link keeps its owner, target, state, and times", async () => {
    const created = await link();

    expect(created).toEqual({
      id: expect.stringMatching(/^[0-9a-f]{24}$/),
      userId: FIRST_USER,
      repository: "pallets/flask",
      version: "3.1.3",
      status: LinkStatus.INDEXING,
      jobId: JOB_ID,
      stage: null,
      progress: null,
      error: null,
      resumeAfter: null,
      indexed: null,
      createdAt: CREATED_AT,
      updatedAt: CREATED_AT,
    });
    expect(await store.findForUser(FIRST_USER, created.id)).toEqual(created);
    expect(await store.findByTarget(FIRST_USER, "pallets/flask", "3.1.3")).toEqual(created);
  });

  test("adding the same version twice returns the first link unchanged", async () => {
    const first = await link();

    const second = await link({ state: readyState(FLASK_BLOCK), now: LATER });

    expect(second).toEqual(first);
    expect(await store.listForUser(FIRST_USER)).toHaveLength(1);
  });

  test("another version, or another person, gets its own link", async () => {
    const first = await link();
    const otherVersion = await link({ version: "3.1.2" });
    const otherPerson = await link({ userId: SECOND_USER });

    expect(new Set([first.id, otherVersion.id, otherPerson.id]).size).toBe(3);
  });

  test("a person cannot find, change, or remove another person's link", async () => {
    const theirs = await link({ userId: SECOND_USER });

    expect(await store.findForUser(FIRST_USER, theirs.id)).toBeNull();
    expect(await store.findByTarget(FIRST_USER, "pallets/flask", "3.1.3")).toBeNull();
    expect(await store.saveState(FIRST_USER, theirs.id, readyState(FLASK_BLOCK), LATER)).toBeNull();
    expect(await store.remove(FIRST_USER, theirs.id)).toBe(false);
    expect(await store.findForUser(SECOND_USER, theirs.id)).toEqual(theirs);
  });

  test.each(["0123456789abcdef01234567", "not-an-id", ""])(
    "link ID %p that does not exist is found nowhere",
    async (linkId) => {
      expect(await store.findForUser(FIRST_USER, linkId)).toBeNull();
      expect(await store.saveState(FIRST_USER, linkId, readyState(FLASK_BLOCK), LATER)).toBeNull();
      expect(await store.remove(FIRST_USER, linkId)).toBe(false);
    },
  );

  test("a person's list holds only their links, newest first", async () => {
    const older = await link({ version: "3.1.2", now: CREATED_AT });
    const newer = await link({ version: "3.1.3", now: LATER });
    await link({ userId: SECOND_USER });

    const listed = await store.listForUser(FIRST_USER);

    expect(listed.map((item) => item.id)).toEqual([newer.id, older.id]);
  });

  test("saving a state replaces every state field and the update time only", async () => {
    const waiting = await link({
      state: {
        ...indexingState(JOB_ID, { stage: "embedding", progress: { embedded: 80, to_embed: 900 } }),
        status: LinkStatus.WAITING_FOR_QUOTA,
        error: { code: "waiting_for_quota", message: "Quota used up" },
        resumeAfter: new Date("2026-10-08T07:00:00Z"),
      },
    });

    const saved = await store.saveState(FIRST_USER, waiting.id, readyState(FLASK_BLOCK), LATER);

    expect(saved).toEqual({
      ...waiting,
      status: LinkStatus.READY,
      jobId: null,
      stage: null,
      progress: null,
      error: null,
      resumeAfter: null,
      indexed: FLASK_BLOCK,
      updatedAt: LATER,
    });
    expect(await store.findForUser(FIRST_USER, waiting.id)).toEqual(saved);
  });

  test("removing a link leaves the person's other links", async () => {
    const removed = await link({ version: "3.1.2" });
    const kept = await link();

    expect(await store.remove(FIRST_USER, removed.id)).toBe(true);

    expect(await store.findForUser(FIRST_USER, removed.id)).toBeNull();
    expect((await store.listForUser(FIRST_USER)).map((item) => item.id)).toEqual([kept.id]);
  });
}

module.exports = { FIRST_USER, FLASK_BLOCK, SECOND_USER, describeRepositoryLinkStoreContract };
