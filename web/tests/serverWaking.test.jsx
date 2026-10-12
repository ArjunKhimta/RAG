import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { wakeServer } from "../src/api/wakeServer.js";
import {
  WAKE_RETRY_MILLISECONDS,
  WAKE_WAIT_MILLISECONDS,
} from "../src/session/SessionProvider.jsx";
import { SIGNED_IN, SIGNED_OUT } from "./support/accounts.js";
import { installFakeApi, jsonReply } from "./support/fakeApi.js";
import { renderApp } from "./support/renderApp.jsx";

vi.mock("../src/api/wakeServer.js", () => ({ wakeServer: vi.fn() }));

const STARTING_UP = /Starting up/;

const CANNOT_BE_REACHED = /cannot be reached/;

function unreachable() {
  throw new TypeError("Failed to fetch");
}

function repliesInOrder(replies) {
  let index = 0;
  return () => {
    const reply = replies[Math.min(index, replies.length - 1)];
    index += 1;
    return typeof reply === "function" ? reply() : reply;
  };
}

function sessionChecks(calls) {
  return calls.filter((call) => call.key === "GET /api/me").length;
}

async function waitOneRetry() {
  await act(() => vi.advanceTimersByTimeAsync(WAKE_RETRY_MILLISECONDS));
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.mocked(wakeServer).mockClear();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("a server waking from sleep", () => {
  it("says it is starting up, then shows the signed-out page once the server answers", async () => {
    const { calls } = installFakeApi({
      "GET /api/me": repliesInOrder([unreachable, unreachable, SIGNED_OUT]),
    });
    renderApp("/");

    expect((await screen.findByRole("status")).textContent).toMatch(STARTING_UP);

    await waitOneRetry();
    await waitOneRetry();

    expect(await screen.findAllByRole("link", { name: "Sign in with GitHub" })).not.toHaveLength(0);
    expect(screen.queryByText(STARTING_UP)).toBeNull();
    expect(sessionChecks(calls)).toBe(3);
    expect(wakeServer).toHaveBeenCalledTimes(1);
  });

  it("does not try to wake a server that answers", async () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });
    renderApp("/");

    expect(await screen.findAllByRole("link", { name: "Sign in with GitHub" })).not.toHaveLength(0);
    expect(wakeServer).not.toHaveBeenCalled();
  });

  it("treats a page that is not the API's JSON as still waking", async () => {
    installFakeApi({
      "GET /api/me": repliesInOrder([jsonReply(200, "<html>Waking up</html>"), SIGNED_IN]),
    });
    renderApp("/");

    expect(await screen.findByText(STARTING_UP)).toBeTruthy();

    await waitOneRetry();

    expect(await screen.findByText(/Signed in as The Octocat/)).toBeTruthy();
  });

  it("treats a gateway error as still waking, even when it is JSON", async () => {
    installFakeApi({
      "GET /api/me": repliesInOrder([jsonReply(503, { error: "Service unavailable" }), SIGNED_OUT]),
    });
    renderApp("/");

    expect(await screen.findByText(STARTING_UP)).toBeTruthy();
  });

  it("shows the waking message on pages that need sign-in too", async () => {
    installFakeApi({ "GET /api/me": unreachable });
    renderApp("/repositories");

    expect(await screen.findByText(STARTING_UP)).toBeTruthy();
  });

  it("gives up after the waiting time, then tries again when asked", async () => {
    const { calls } = installFakeApi({ "GET /api/me": unreachable });
    renderApp("/");
    await screen.findByText(STARTING_UP);

    await act(() => vi.advanceTimersByTimeAsync(WAKE_WAIT_MILLISECONDS));

    expect((await screen.findByRole("alert")).textContent).toMatch(CANNOT_BE_REACHED);
    const checksWhenGivingUp = sessionChecks(calls);
    expect(wakeServer).toHaveBeenCalledTimes(1);
    expect(checksWhenGivingUp).toBe(WAKE_WAIT_MILLISECONDS / WAKE_RETRY_MILLISECONDS + 1);

    await act(() => vi.advanceTimersByTimeAsync(WAKE_WAIT_MILLISECONDS));
    expect(sessionChecks(calls)).toBe(checksWhenGivingUp);

    installFakeApi({ "GET /api/me": SIGNED_OUT });
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findAllByRole("link", { name: "Sign in with GitHub" })).not.toHaveLength(0);
    expect(screen.queryByText(CANNOT_BE_REACHED)).toBeNull();
  });

  it("shows a real error from the API at once, without waiting", async () => {
    const { calls } = installFakeApi({
      "GET /api/me": jsonReply(500, { error: "Something went wrong" }),
    });
    renderApp("/");

    expect((await screen.findByRole("alert")).textContent).toMatch(CANNOT_BE_REACHED);
    expect(screen.queryByText(STARTING_UP)).toBeNull();

    await waitOneRetry();
    expect(sessionChecks(calls)).toBe(1);
    expect(wakeServer).not.toHaveBeenCalled();
  });

  it("stops asking once the page is closed", async () => {
    const { calls } = installFakeApi({ "GET /api/me": unreachable });
    const { unmount } = renderApp("/");
    await screen.findByText(STARTING_UP);

    unmount();
    await act(() => vi.advanceTimersByTimeAsync(WAKE_RETRY_MILLISECONDS * 5));

    expect(sessionChecks(calls)).toBe(1);
  });
});
