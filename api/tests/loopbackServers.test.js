const http = require("node:http");
const request = require("supertest");

const openServers = [];

afterEach(async () => {
  const closing = openServers.splice(0).map(
    (server) => new Promise((resolve) => server.close(() => resolve())),
  );
  await Promise.all(closing);
});

function listen(server, ...args) {
  openServers.push(server);
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(...args, () => resolve(server.address()));
  });
}

function answering(text, status = 200) {
  return http.createServer((incoming, outgoing) => {
    outgoing.writeHead(status);
    outgoing.end(text);
  });
}

test("a server listening on every address reports the IPv6 loopback", async () => {
  const address = await listen(http.createServer(), 0);

  expect(address.address).toBe("::1");
});

test("supertest reaches the app even when another program holds 127.0.0.1 at the same port", async () => {
  const { port } = await listen(answering("another program", 404), 0, "127.0.0.1");
  const appUnderTest = answering("the app under test");
  await listen(appUnderTest, port);

  const response = await request(appUnderTest).get("/");

  expect(response.text).toBe("the app under test");
});

test("a server that names its own address still reports it", async () => {
  const address = await listen(http.createServer(), 0, "127.0.0.1");

  expect(address.address).toBe("127.0.0.1");
});
