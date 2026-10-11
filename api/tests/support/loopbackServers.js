/**
 * Make supertest reach the app under test, never another program on this machine.
 *
 * supertest starts each app with `listen(0)`, which listens on every address at a random port,
 * and then sends its requests to 127.0.0.1. On macOS another program may already listen on
 * 127.0.0.1 at that same port number (a VS Code helper did, at 53267), and the system lets both
 * hold it; a request to 127.0.0.1 then reaches that program instead of the test's app, and a test
 * fails at random with an answer it never sent (seen as 404s and missing redirects, about one run
 * in ten). Listening on 127.0.0.1 itself is not possible here: a server given an address finds
 * it asynchronously, and supertest reads the address at once.
 *
 * So a server listening on every address (`::`) reports the IPv6 loopback, `::1`, as its address,
 * and supertest connects there, where no program holding 127.0.0.1 can answer. Only a program
 * listening on `::1` at the same port could still intercept, and none does on the machines used
 * so far. Servers that name their own address, like the fake services, are unchanged. Loaded for
 * every test file through Jest's `setupFiles`.
 */

const net = require("node:net");

const EVERY_ADDRESS = "::";

const IPV6_LOOPBACK = "::1";

const reportAddress = net.Server.prototype.address;

net.Server.prototype.address = function addressOnLoopback() {
  const address = reportAddress.call(this);
  if (address !== null && typeof address === "object" && address.address === EVERY_ADDRESS) {
    return { ...address, address: IPV6_LOOPBACK };
  }
  return address;
};
