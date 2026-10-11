import { apiRequest } from "./client.js";

const CONVERSATIONS_PATH = "/api/conversations";

function conversationPath(conversationId) {
  return `${CONVERSATIONS_PATH}/${encodeURIComponent(conversationId)}`;
}

export async function listConversations() {
  const reply = await apiRequest(CONVERSATIONS_PATH);
  return reply.conversations;
}

export function startConversation(linkId) {
  return apiRequest(CONVERSATIONS_PATH, { method: "POST", body: { link_id: linkId } });
}

export function fetchConversation(conversationId) {
  return apiRequest(conversationPath(conversationId));
}

export function askQuestion(conversationId, question, excludeTests) {
  return apiRequest(`${conversationPath(conversationId)}/messages`, {
    method: "POST",
    body: { question, exclude_tests: excludeTests },
  });
}

export function deleteConversation(conversationId) {
  return apiRequest(conversationPath(conversationId), { method: "DELETE" });
}
