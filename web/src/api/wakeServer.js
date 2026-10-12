/**
 * Wakes the Node API when its free host has put it to sleep. Every other request goes through
 * the site's own `/api` path; this one goes straight to the API's own address, carrying no
 * cookies and no referrer, and its reply is never read: reaching the host is what starts the API,
 * and the API wakes the retrieval service as it starts. Never sent from a local copy of the site,
 * so local work does not reach the live server.
 */

import { API_ORIGIN } from "../../apiOrigin.js";

export const WAKE_URL = `${API_ORIGIN}/health`;

const LOCAL_HOSTNAMES = new Set(["localhost", "127.0.0.1", "[::1]"]);

export function wakeServer(hostname = window.location.hostname) {
  if (LOCAL_HOSTNAMES.has(hostname)) {
    return;
  }
  fetch(WAKE_URL, {
    mode: "no-cors",
    credentials: "omit",
    cache: "no-store",
    referrerPolicy: "no-referrer",
  }).catch(ignoreFailure);
}

function ignoreFailure() {}
