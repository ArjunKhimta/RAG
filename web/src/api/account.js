import { ApiRequestError, apiRequest } from "./client.js";

export const SIGN_IN_PATH = "/api/auth/github/login";

const UNAUTHORIZED_STATUS = 401;

export function isSignedOutError(error) {
  return error instanceof ApiRequestError && error.status === UNAUTHORIZED_STATUS;
}

export async function fetchCurrentUser() {
  try {
    return await apiRequest("/api/me");
  } catch (error) {
    if (isSignedOutError(error)) {
      return null;
    }
    throw error;
  }
}

export function fetchUsage() {
  return apiRequest("/api/me/usage");
}

export function signOutEverywhere() {
  return apiRequest("/api/auth/logout", { method: "POST" });
}

export function deleteAccount() {
  return apiRequest("/api/me", { method: "DELETE" });
}
