import { describe, expect, it } from "vitest";
import { highlightPythonLines, TokenKind } from "../src/code/pythonHighlighter.js";
import { EXAMPLE_ANSWER } from "../src/home/exampleAnswer.js";

function tokensOf(line) {
  return highlightPythonLines([line])[0];
}

function kindsOf(line) {
  return tokensOf(line)
    .filter((token) => token.kind !== TokenKind.PLAIN)
    .map((token) => [token.kind, token.text]);
}

function textOf(tokens) {
  return tokens.map((token) => token.text).join("");
}

describe("python highlighter", () => {
  it("keeps every character of every line, in order", () => {
    const lines = EXAMPLE_ANSWER.lines.map((line) => line.text);

    const highlighted = highlightPythonLines(lines);

    expect(highlighted.map(textOf)).toEqual(lines);
  });

  it("marks keywords, the defined name, self, types, and the return annotation", () => {
    expect(
      kindsOf("    def get_signing_serializer(self, app: Flask) -> URLSafeTimedSerializer | None:"),
    ).toEqual([
      [TokenKind.KEYWORD, "def"],
      [TokenKind.DEFINITION, "get_signing_serializer"],
      [TokenKind.SELF, "self"],
      [TokenKind.TYPE, "Flask"],
      [TokenKind.TYPE, "URLSafeTimedSerializer"],
      [TokenKind.KEYWORD, "None"],
    ]);
  });

  it("marks a class name after class, and calls by the parenthesis after them", () => {
    expect(kindsOf("class SessionInterface(Base):")).toEqual([
      [TokenKind.KEYWORD, "class"],
      [TokenKind.TYPE, "SessionInterface"],
      [TokenKind.TYPE, "Base"],
    ]);
    expect(kindsOf("keys.extend (fallbacks)")).toEqual([[TokenKind.CALL, "extend"]]);
  });

  it("leaves constants in capitals as plain names, not types", () => {
    expect(kindsOf("SECRET_KEY = None")).toEqual([[TokenKind.KEYWORD, "None"]]);
  });

  it("ends a comment at the end of the line and ignores a # inside a string", () => {
    expect(kindsOf('url = "a#b"  # the anchor')).toEqual([
      [TokenKind.STRING, '"a#b"'],
      [TokenKind.COMMENT, "# the anchor"],
    ]);
  });

  it("does not end a string at an escaped quote", () => {
    expect(kindsOf('text = "say \\"hi\\"" + x')).toEqual([[TokenKind.STRING, '"say \\"hi\\""']]);
  });

  it("colours prefixed strings, including f-strings with braces inside", () => {
    expect(kindsOf('rb"\\d+" f"{name!r}"')).toEqual([
      [TokenKind.STRING, 'rb"\\d+"'],
      [TokenKind.STRING, 'f"{name!r}"'],
    ]);
  });

  it("carries a triple-quoted string across lines until it closes", () => {
    const highlighted = highlightPythonLines([
      'doc = """First line',
      "still inside # not a comment",
      'last line""" + tail',
      "after = 1",
    ]);

    expect(highlighted[0].at(-1)).toEqual({ kind: TokenKind.STRING, text: '"""First line' });
    expect(highlighted[1]).toEqual([
      { kind: TokenKind.STRING, text: "still inside # not a comment" },
    ]);
    expect(highlighted[2][0]).toEqual({ kind: TokenKind.STRING, text: 'last line"""' });
    expect(highlighted[3].map((token) => token.kind)).toContain(TokenKind.NUMBER);
  });

  it("closes a single-quoted string at the end of the line when it is left open", () => {
    const highlighted = highlightPythonLines(["broken = 'no end", "next = 2"]);

    expect(highlighted[0].at(-1)).toEqual({ kind: TokenKind.STRING, text: "'no end" });
    expect(highlighted[1].some((token) => token.kind === TokenKind.STRING)).toBe(false);
  });

  it("marks decorators at the start of a line, but not matrix multiplication", () => {
    expect(kindsOf("    @app.route")).toEqual([[TokenKind.DECORATOR, "@app.route"]]);
    expect(kindsOf("c = a @ b")).toEqual([]);
  });

  it("marks numbers in their Python forms, but not digits inside names", () => {
    expect(kindsOf("x = 0x1F + 1_000 + 2.5e-3 + 3j + .5")).toEqual([
      [TokenKind.NUMBER, "0x1F"],
      [TokenKind.NUMBER, "1_000"],
      [TokenKind.NUMBER, "2.5e-3"],
      [TokenKind.NUMBER, "3j"],
      [TokenKind.NUMBER, ".5"],
    ]);
    expect(kindsOf("value2 = item3")).toEqual([]);
  });

  it("treats an empty or missing line as nothing to colour", () => {
    expect(highlightPythonLines(["", null])).toEqual([[], []]);
  });

  it("keeps markup-like text as plain characters", () => {
    const line = 'html = "<img src=x onerror=alert(1)>"';

    expect(textOf(tokensOf(line))).toBe(line);
  });
});
