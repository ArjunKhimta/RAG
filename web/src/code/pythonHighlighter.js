export const TokenKind = Object.freeze({
  PLAIN: "plain",
  KEYWORD: "keyword",
  STRING: "string",
  COMMENT: "comment",
  NUMBER: "number",
  DECORATOR: "decorator",
  DEFINITION: "definition",
  CALL: "call",
  TYPE: "type",
  SELF: "self",
});

const KEYWORDS = new Set([
  "False",
  "None",
  "True",
  "and",
  "as",
  "assert",
  "async",
  "await",
  "break",
  "class",
  "continue",
  "def",
  "del",
  "elif",
  "else",
  "except",
  "finally",
  "for",
  "from",
  "global",
  "if",
  "import",
  "in",
  "is",
  "lambda",
  "nonlocal",
  "not",
  "or",
  "pass",
  "raise",
  "return",
  "try",
  "while",
  "with",
  "yield",
]);

const DEFINING_KEYWORDS = new Set(["def", "class"]);

const SELF_NAMES = new Set(["self", "cls"]);

const BUILT_IN_TYPES = new Set([
  "bool",
  "bytearray",
  "bytes",
  "complex",
  "dict",
  "float",
  "frozenset",
  "int",
  "list",
  "object",
  "set",
  "str",
  "tuple",
  "type",
]);

const IDENTIFIER = /^[\p{L}_][\p{L}\p{N}_]*/u;

const NUMBER =
  /^(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d[\d_]*)?[jJ]?)/;

const STRING_START = /^([rRbBuUfF]{0,2})("""|'''|"|')/;

const DECORATOR = /^@[\p{L}_][\p{L}\p{N}_.]*/u;

const WHITESPACE = /^\s+/;

const CAPITALIZED_NAME = /^_?[A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*$/;

export function highlightPythonLines(lineTexts) {
  let openString = null;
  return lineTexts.map((lineText) => {
    const { tokens, stillOpen } = highlightLine(lineText ?? "", openString);
    openString = stillOpen;
    return tokens;
  });
}

function highlightLine(lineText, openString) {
  const tokens = [];
  let position = 0;
  let stillOpen = openString;
  let previousWord = null;
  let atLineStart = true;

  if (stillOpen !== null) {
    const closing = findClosingQuote(lineText, 0, stillOpen);
    if (closing === -1) {
      pushToken(tokens, TokenKind.STRING, lineText);
      return { tokens, stillOpen };
    }
    pushToken(tokens, TokenKind.STRING, lineText.slice(0, closing));
    position = closing;
    stillOpen = null;
    atLineStart = false;
  }

  while (position < lineText.length) {
    const rest = lineText.slice(position);

    const whitespace = WHITESPACE.exec(rest);
    if (whitespace !== null) {
      pushToken(tokens, TokenKind.PLAIN, whitespace[0]);
      position += whitespace[0].length;
      continue;
    }

    if (rest.startsWith("#")) {
      pushToken(tokens, TokenKind.COMMENT, rest);
      break;
    }

    const stringStart = STRING_START.exec(rest);
    if (stringStart !== null && isStringPrefixAllowed(stringStart[1])) {
      const quote = stringStart[2];
      const bodyStart = position + stringStart[0].length;
      const closing = findClosingQuote(lineText, bodyStart, quote);
      if (closing === -1) {
        pushToken(tokens, TokenKind.STRING, rest);
        stillOpen = quote.length === 3 ? quote : null;
        break;
      }
      pushToken(tokens, TokenKind.STRING, lineText.slice(position, closing));
      position = closing;
      previousWord = null;
      atLineStart = false;
      continue;
    }

    if (atLineStart) {
      const decorator = DECORATOR.exec(rest);
      if (decorator !== null) {
        pushToken(tokens, TokenKind.DECORATOR, decorator[0]);
        position += decorator[0].length;
        atLineStart = false;
        continue;
      }
    }

    const identifier = IDENTIFIER.exec(rest);
    if (identifier !== null) {
      const word = identifier[0];
      const after = lineText.slice(position + word.length);
      pushToken(tokens, kindOfWord(word, previousWord, after), word);
      previousWord = word;
      position += word.length;
      atLineStart = false;
      continue;
    }

    const number = NUMBER.exec(rest);
    if (number !== null) {
      pushToken(tokens, TokenKind.NUMBER, number[0]);
      position += number[0].length;
      previousWord = null;
      atLineStart = false;
      continue;
    }

    pushToken(tokens, TokenKind.PLAIN, rest[0]);
    position += 1;
    previousWord = null;
    atLineStart = false;
  }

  return { tokens, stillOpen };
}

function isStringPrefixAllowed(prefix) {
  return prefix.length < 2 || /^(?:[rR][bBfF]|[bBfF][rR])$/.test(prefix);
}

function findClosingQuote(lineText, start, quote) {
  let index = start;
  while (index < lineText.length) {
    if (lineText[index] === "\\") {
      index += 2;
      continue;
    }
    if (lineText.startsWith(quote, index)) {
      return index + quote.length;
    }
    index += 1;
  }
  return -1;
}

function kindOfWord(word, previousWord, after) {
  if (DEFINING_KEYWORDS.has(previousWord)) {
    return previousWord === "class" ? TokenKind.TYPE : TokenKind.DEFINITION;
  }
  if (KEYWORDS.has(word)) {
    return TokenKind.KEYWORD;
  }
  if (SELF_NAMES.has(word)) {
    return TokenKind.SELF;
  }
  if (BUILT_IN_TYPES.has(word) || CAPITALIZED_NAME.test(word)) {
    return TokenKind.TYPE;
  }
  if (/^\s*\(/.test(after)) {
    return TokenKind.CALL;
  }
  return TokenKind.PLAIN;
}

function pushToken(tokens, kind, text) {
  if (text.length === 0) {
    return;
  }
  const last = tokens[tokens.length - 1];
  if (last !== undefined && last.kind === kind) {
    last.text += text;
    return;
  }
  tokens.push({ kind, text });
}
