const {
  nextPacificMidnight,
  pacificDay,
  secondsUntilPacificMidnight,
} = require("../src/pacificDay");

test.each([
  ["2026-10-07T06:59:59Z", "2026-10-06"],
  ["2026-10-07T07:00:00Z", "2026-10-07"],
  ["2026-12-15T07:59:59Z", "2026-12-14"],
  ["2026-12-15T08:00:00Z", "2026-12-15"],
])("at %s it is %s in Pacific time", (instant, day) => {
  expect(pacificDay(Date.parse(instant))).toBe(day);
});

test.each([
  ["a summer day", "2026-10-07T20:30:00Z", "2026-10-08T07:00:00Z"],
  ["a winter day", "2026-12-15T20:30:00Z", "2026-12-16T08:00:00Z"],
  ["the day the clocks go back (25 hours)", "2026-11-01T12:00:00Z", "2026-11-02T08:00:00Z"],
  ["the day the clocks go forward (23 hours)", "2026-03-08T12:00:00Z", "2026-03-09T07:00:00Z"],
  ["the last second of a day", "2026-10-08T06:59:59Z", "2026-10-08T07:00:00Z"],
  ["the first second of a day", "2026-10-08T07:00:00Z", "2026-10-09T07:00:00Z"],
])("on %s the next midnight is %s", (description, instant, midnight) => {
  expect(nextPacificMidnight(Date.parse(instant)).toISOString()).toBe(
    new Date(midnight).toISOString(),
  );
});

test("whole days measure 25 and 23 hours when the clocks change", () => {
  const fallBackStart = Date.parse("2026-11-01T07:00:00Z");
  const springForwardStart = Date.parse("2026-03-08T08:00:00Z");

  expect(secondsUntilPacificMidnight(fallBackStart)).toBe(25 * 3600);
  expect(secondsUntilPacificMidnight(springForwardStart)).toBe(23 * 3600);
});

test("the wait is rounded up and never zero", () => {
  expect(secondsUntilPacificMidnight(Date.parse("2026-10-08T06:59:59.500Z"))).toBe(1);
});
