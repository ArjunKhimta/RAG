import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { confirmationMatches } from "../src/components/DeleteAccountForm.jsx";
import { formatResetTime } from "../src/components/UsagePanel.jsx";
import { NO_CONTENT, SIGNED_IN, SIGNED_OUT, USAGE } from "./support/accounts.js";
import { installFakeApi, jsonReply } from "./support/fakeApi.js";
import { renderApp } from "./support/renderApp.jsx";

function signedInApi(extraRoutes = {}) {
  return installFakeApi({
    "GET /api/me": SIGNED_IN,
    "GET /api/me/usage": USAGE,
    ...extraRoutes,
  });
}

describe("account page", () => {
  it("asks a signed-out visitor to sign in", async () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/account");

    expect(await screen.findByText("Sign in to see your account.")).toBeTruthy();
  });

  it("shows the profile and today's usage", async () => {
    signedInApi();

    renderApp("/account");

    expect(await screen.findByText("The Octocat")).toBeTruthy();
    expect(screen.getByText("octocat on GitHub")).toBeTruthy();
    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row").map((row) => row.textContent);
    expect(rows).toEqual([
      "Questions today3 of 30",
      "Searches today12 of 100",
      "New repositories to index today1 of 2",
      "Linked repositories2 of 5",
    ]);
    expect(screen.getByText(/Daily counts reset at midnight Pacific time/).textContent).toContain(
      formatResetTime("2026-10-12T07:00:00.000Z"),
    );
  });

  it("explains when usage cannot be loaded", async () => {
    signedInApi({ "GET /api/me/usage": jsonReply(500, { error: "Something went wrong" }) });

    renderApp("/account");

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Could not load your usage: Something went wrong",
    );
  });

  it("treats a session that ended while loading usage as signed out", async () => {
    signedInApi({ "GET /api/me/usage": SIGNED_OUT });

    renderApp("/account");

    expect(await screen.findByText("Sign in to see your account.")).toBeTruthy();
  });
});

describe("signing out", () => {
  it("signs out everywhere and says so on the home page", async () => {
    const { calls } = signedInApi({ "POST /api/auth/logout": NO_CONTENT });
    renderApp("/account");

    await userEvent.click(await screen.findByRole("button", { name: "Sign out on every device" }));

    expect(await screen.findByText("You are signed out on every device.")).toBeTruthy();
    expect(calls.map((call) => call.key)).toContain("POST /api/auth/logout");
    expect(screen.getAllByRole("link", { name: "Sign in with GitHub" }).length).toBeGreaterThan(0);
  });

  it("stays signed in and explains when signing out fails", async () => {
    signedInApi({ "POST /api/auth/logout": jsonReply(500, { error: "Something went wrong" }) });
    renderApp("/account");

    await userEvent.click(await screen.findByRole("button", { name: "Sign out on every device" }));

    expect(await screen.findByText("Could not sign out: Something went wrong")).toBeTruthy();
    expect(screen.getByRole("link", { name: "octocat" })).toBeTruthy();
  });
});

describe("deleting the account", () => {
  it("keeps the delete button disabled until the username is typed", async () => {
    signedInApi();
    renderApp("/account");
    const button = await screen.findByRole("button", { name: "Delete my account" });
    const input = screen.getByLabelText(/Type your GitHub username/);

    expect(button.disabled).toBe(true);
    await userEvent.type(input, "octo");
    expect(button.disabled).toBe(true);
    await userEvent.type(input, "cat");
    expect(button.disabled).toBe(false);
  });

  it("compares the username without regard to case or surrounding spaces", () => {
    expect(confirmationMatches("  OctoCat ", "octocat")).toBe(true);
    expect(confirmationMatches("octocat2", "octocat")).toBe(false);
    expect(confirmationMatches("", "octocat")).toBe(false);
  });

  it("deletes the account and says so on the home page", async () => {
    const { calls } = signedInApi({ "DELETE /api/me": NO_CONTENT });
    renderApp("/account");

    await userEvent.type(await screen.findByLabelText(/Type your GitHub username/), "octocat");
    await userEvent.click(screen.getByRole("button", { name: "Delete my account" }));

    expect(
      await screen.findByText("Your account and everything stored about you have been deleted."),
    ).toBeTruthy();
    expect(calls.filter((call) => call.key === "DELETE /api/me")).toHaveLength(1);
    expect(screen.queryByRole("link", { name: "octocat" })).toBeNull();
  });

  it("stays on the page and explains when deleting fails", async () => {
    signedInApi({ "DELETE /api/me": jsonReply(500, { error: "Something went wrong" }) });
    renderApp("/account");

    await userEvent.type(await screen.findByLabelText(/Type your GitHub username/), "octocat");
    await userEvent.click(screen.getByRole("button", { name: "Delete my account" }));

    expect(
      await screen.findByText("Could not delete your account: Something went wrong"),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Delete my account" }).disabled).toBe(false);
  });

  it("links to GitHub's authorized apps without sending a referrer", async () => {
    signedInApi();
    renderApp("/account");

    const link = await screen.findByRole("link", { name: "GitHub's authorized apps" });

    expect(link.getAttribute("href")).toBe("https://github.com/settings/applications");
    expect(link.getAttribute("rel")).toBe("noreferrer");
  });
});
