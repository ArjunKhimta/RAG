const { PRUNE_THRESHOLD, createSlidingWindowLimiter } = require("../src/slidingWindowLimiter");
const { createClock } = require("./support/testApp");

let clock;
let limiter;

beforeEach(() => {
  clock = createClock();
  limiter = createSlidingWindowLimiter({ limit: 3, windowMilliseconds: 60_000, now: clock.now });
});

test("a key gets the limit and is then told how long until the oldest use leaves the window", () => {
  limiter.take("person");
  clock.advanceSeconds(10);
  limiter.take("person");
  limiter.take("person");

  const refused = limiter.take("person");

  expect(refused).toEqual({ taken: false, retryAfterSeconds: 50 });
});

test("a slot frees exactly when the oldest use is a full window old", () => {
  limiter.take("person");
  limiter.take("person");
  limiter.take("person");

  clock.advanceSeconds(59.999);
  expect(limiter.take("person").taken).toBe(false);
  clock.advanceSeconds(0.001);
  expect(limiter.take("person").taken).toBe(true);
});

test("a burst straddling a minute boundary still gets only the limit", () => {
  clock.advanceSeconds(59);
  limiter.take("person");
  limiter.take("person");
  limiter.take("person");
  clock.advanceSeconds(2);

  expect(limiter.take("person").taken).toBe(false);
});

test("keys are counted separately", () => {
  limiter.take("first");
  limiter.take("first");
  limiter.take("first");

  expect(limiter.take("second").taken).toBe(true);
});

test("a slot given back can be used again", () => {
  limiter.take("person");
  limiter.take("person");
  const third = limiter.take("person");

  limiter.giveBack("person", third.time);

  expect(limiter.take("person").taken).toBe(true);
  expect(limiter.take("person").taken).toBe(false);
});

test("giving back a slot that is not held changes nothing", () => {
  limiter.giveBack("nobody", clock.now());
  limiter.take("person");

  limiter.giveBack("person", clock.now() - 1);

  expect(limiter.take("person").taken).toBe(true);
  expect(limiter.take("person").taken).toBe(true);
  expect(limiter.take("person").taken).toBe(false);
});

test("keys with no recent use are dropped once the map grows large", () => {
  for (let index = 0; index <= PRUNE_THRESHOLD; index += 1) {
    limiter.take(`address-${index}`);
  }
  clock.advanceSeconds(61);

  limiter.take("newcomer");

  expect(limiter.size()).toBe(1);
});
