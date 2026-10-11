import { apiRequest } from "./client.js";

const REPOSITORIES_PATH = "/api/repositories";

function linkPath(linkId) {
  return `${REPOSITORIES_PATH}/${encodeURIComponent(linkId)}`;
}

export async function listRepositories() {
  const reply = await apiRequest(REPOSITORIES_PATH);
  return reply.repositories;
}

export function addRepository(repository, version) {
  return apiRequest(REPOSITORIES_PATH, { method: "POST", body: { repository, version } });
}

export function fetchRepository(linkId) {
  return apiRequest(linkPath(linkId));
}

export function resumeRepository(linkId) {
  return apiRequest(`${linkPath(linkId)}/resume`, { method: "POST" });
}

export function removeRepository(linkId) {
  return apiRequest(linkPath(linkId), { method: "DELETE" });
}

export function searchRepository(linkId, { query, limit, excludeTests }) {
  return apiRequest(`${linkPath(linkId)}/search`, {
    method: "POST",
    body: { query, limit, exclude_tests: excludeTests },
  });
}
