const fs = require("node:fs");
const path = require("node:path");
const packageJson = require("../package.json");

const API_ROOT = path.join(__dirname, "..");
const dockerfile = fs.readFileSync(path.join(API_ROOT, "Dockerfile"), "utf8");
const dockerignore = fs.readFileSync(path.join(API_ROOT, ".dockerignore"), "utf8");
const nodeVersionFile = fs.readFileSync(path.join(API_ROOT, ".node-version"), "utf8").trim();

const PINNED_BASE_IMAGE = /^FROM node:(\d+)\.\d+\.\d+-[\w-]*slim@sha256:[0-9a-f]{64}$/m;

function instructions(keyword) {
  return dockerfile
    .replace(/\\\n/g, " ")
    .split("\n")
    .filter((line) => line.startsWith(`${keyword} `));
}

test("the base image is an exact Node release pinned by digest", () => {
  expect(dockerfile).toMatch(PINNED_BASE_IMAGE);
  expect(instructions("FROM")).toHaveLength(1);
});

test("the image runs the Node major version the project requires", () => {
  const major = dockerfile.match(PINNED_BASE_IMAGE)[1];

  expect(major).toBe(nodeVersionFile);
  expect(packageJson.engines.node).toBe(`>=${major} <${Number(major) + 1}`);
});

test("packages come from the lockfile without development packages or install scripts", () => {
  const installs = instructions("RUN").filter((line) => line.includes("npm "));

  expect(installs).toHaveLength(1);
  expect(installs[0]).toContain("npm ci --omit=dev --ignore-scripts");
  expect(installs[0]).toContain("npm audit signatures");
});

test("the API runs as the unprivileged node user", () => {
  expect(instructions("USER").at(-1)).toBe("USER node");
});

test("only named files reach the builder and never an env file", () => {
  const rules = dockerignore.split("\n").filter((line) => line && !line.startsWith("#"));
  const allowed = rules.filter((rule) => rule.startsWith("!"));

  expect(rules[0]).toBe("*");
  expect(allowed.sort()).toEqual(["!.npmrc", "!package-lock.json", "!package.json", "!src/"]);
});
