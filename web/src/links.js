const GITHUB_PREFIX = "https://github.com/";

export function safeGitHubLink(url) {
  return typeof url === "string" && url.startsWith(GITHUB_PREFIX) ? url : null;
}

export function gitHubRepositoryLink(repository) {
  return `${GITHUB_PREFIX}${repository}`;
}

export function gitHubCommitLink(repository, commitId) {
  return `${GITHUB_PREFIX}${repository}/tree/${commitId}`;
}
