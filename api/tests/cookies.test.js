const { readCookie } = require("../src/cookies");

function requestWithCookie(header) {
  return { get: (name) => (name === "Cookie" ? header : undefined) };
}

test.each([
  ["session=abc", "abc"],
  ["theme=dark; session=abc; other=1", "abc"],
  ["  session = abc  ", "abc"],
  ["session=a%20b", "a b"],
  ["session=first; session=second", "first"],
  ["session=", ""],
  ["session=a=b", "a=b"],
])("%p gives the session cookie %p", (header, expected) => {
  expect(readCookie(requestWithCookie(header), "session")).toBe(expected);
});

test.each([undefined, "", "theme=dark", "sessions=abc", "xsession=abc", "session", "session=%E0%A4"])(
  "%p gives no session cookie",
  (header) => {
    expect(readCookie(requestWithCookie(header), "session")).toBeNull();
  },
);
