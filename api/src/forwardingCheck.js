/**
 * `GET /api/forwarding-check`: shows the caller how their request reached the API, so the number
 * of proxies in front of it (`TRUST_PROXY_HOPS`) can be counted on a new host rather than guessed.
 *
 * The reply holds only the caller's own request: each address in `X-Forwarded-For`, the address
 * of the connection that delivered the request (the nearest proxy), and the address Express takes
 * as the visitor's with the current setting. Nothing is logged or stored. The route exists only
 * while `FORWARDING_CHECK=on`; otherwise the path is an ordinary 404.
 */

const express = require("express");

const NO_STORE = "no-store";

/** @param {{ trustProxyHops: number }} options */
function createForwardingCheckRouter({ trustProxyHops }) {
  const router = express.Router();
  router.get("/forwarding-check", (request, response) => {
    response.set("Cache-Control", NO_STORE);
    response.json({
      trust_proxy_hops: trustProxyHops,
      forwarded_for: forwardedAddresses(request.get("X-Forwarded-For")),
      connection_address: request.socket.remoteAddress ?? null,
      address_used: request.ip ?? null,
    });
  });
  return router;
}

/** @param {string | undefined} header */
function forwardedAddresses(header) {
  if (!header) {
    return [];
  }
  return header
    .split(",")
    .map((address) => address.trim())
    .filter((address) => address.length > 0);
}

module.exports = { createForwardingCheckRouter };
