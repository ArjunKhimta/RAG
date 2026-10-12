/**
 * Wakes the Node API and the retrieval service when their free host has put them to sleep.
 * Render wakes a sleeping service only for a request sent straight to its own address: one
 * forwarded by Vercel, or sent by the API to the service, is refused with 502 instead. So the page
 * calls both health checks directly, carrying no cookies and no referrer, and never reads the
 * replies: reaching the host is what starts each server. Sent when the page loads and when a
 * request finds a server asleep, at most once every `WAKE_INTERVAL_MILLISECONDS`. Never sent from
 * a local copy of the site, so local work does not reach the live servers.
 */

import { API_ORIGIN, RETRIEVAL_SERVICE_ORIGIN } from "../../apiOrigin.js";

export const WAKE_URLS = Object.freeze([
  `${API_ORIGIN}/health`,
  `${RETRIEVAL_SERVICE_ORIGIN}/health`,
]);

export const WAKE_INTERVAL_MILLISECONDS = 20_000;

const LOCAL_HOSTNAMES = new Set(["localhost", "127.0.0.1", "[::1]"]);

let lastWakeAt = null;

export function wakeServers(hostname = window.location.hostname, now = Date.now()) {
  if (LOCAL_HOSTNAMES.has(hostname)) {
    return;
  }
  if (lastWakeAt !== null && now - lastWakeAt < WAKE_INTERVAL_MILLISECONDS) {
    return;
  }
  lastWakeAt = now;
  for (const url of WAKE_URLS) {
    fetch(url, {
      mode: "no-cors",
      credentials: "omit",
      cache: "no-store",
      referrerPolicy: "no-referrer",
    }).catch(ignoreFailure);
  }
}

function ignoreFailure() {}
