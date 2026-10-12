import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { signInMessageFor } from "../src/components/SignInNotice.jsx";
import { OCTOCAT, SIGNED_IN, SIGNED_OUT } from "./support/accounts.js";
import { installFakeApi, jsonReply } from "./support/fakeApi.js";
import { renderApp } from "./support/renderApp.jsx";

describe("signing in", () => {
  it("offers GitHub sign-in to a signed-out visitor", async () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/");

    const buttons = await screen.findAllByRole("link", { name: "Sign in with GitHub" });
    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
      expect(button.getAttribute("href")).toBe("/api/auth/github/login");
    }
  });

  it("shows the signed-in person in the header with a link to their account", async () => {
    installFakeApi({ "GET /api/me": SIGNED_IN });

    renderApp("/");

    const accountLink = await screen.findByRole("link", { name: "octocat" });
    expect(accountLink.getAttribute("href")).toBe("/account");
    expect(accountLink.querySelector("img").getAttribute("src")).toBe(OCTOCAT.avatar_url);
    expect(screen.queryByRole("link", { name: "Sign in with GitHub" })).toBeNull();
    expect(screen.getByText(/Signed in as The Octocat/)).toBeTruthy();
  });

  it("uses the login when the person has no name", async () => {
    installFakeApi({ "GET /api/me": jsonReply(200, { ...OCTOCAT, name: null }) });

    renderApp("/");

    expect(await screen.findByText(/Signed in as octocat/)).toBeTruthy();
  });

  it("shows neither sign-in nor an account while the session is loading", () => {
    installFakeApi({ "GET /api/me": () => new Promise(() => {}) });

    renderApp("/");

    expect(screen.queryByRole("link", { name: "Sign in with GitHub" })).toBeNull();
    expect(screen.queryByRole("link", { name: "octocat" })).toBeNull();
  });

  it("says the server cannot be reached instead of offering sign-in", async () => {
    installFakeApi({ "GET /api/me": jsonReply(500, { error: "Something went wrong" }) });

    renderApp("/");

    expect(await screen.findByText(/The server cannot be reached right now/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Sign in with GitHub" })).toBeNull();
  });
});

describe("sign-in outcomes", () => {
  it.each([
    ["denied", "Sign-in was cancelled on GitHub, so nothing was saved."],
    ["expired", /took longer than 10 minutes or started in another browser/],
    ["failed", "Sign-in did not work. Please try again in a moment."],
    ["rate_limited", /Please wait up to 15 minutes/],
  ])("explains the %s outcome", async (outcome, message) => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp(`/?sign_in=${outcome}`);

    expect((await screen.findByRole("alert")).textContent).toMatch(message);
  });

  it("ignores an outcome it does not know", () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/?sign_in=<script>alert(1)</script>");

    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("never treats inherited object names as outcomes", () => {
    expect(signInMessageFor("toString")).toBeNull();
    expect(signInMessageFor("__proto__")).toBeNull();
    expect(signInMessageFor(null)).toBeNull();
  });

  it("removes the message when dismissed", async () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });
    renderApp("/?sign_in=denied");

    await userEvent.click(await screen.findByRole("button", { name: "Dismiss" }));

    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });
});
