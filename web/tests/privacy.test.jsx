import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { GEMINI_TERMS_URL } from "../src/privacy/policy.js";
import { SIGNED_IN, SIGNED_OUT, USAGE } from "./support/accounts.js";
import { conversation, message } from "./support/answers.js";
import { installFakeApi, jsonReply } from "./support/fakeApi.js";
import { renderApp } from "./support/renderApp.jsx";

function tableRows(caption) {
  const table = screen.getByRole("columnheader", { name: caption }).closest("table");
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) => row.textContent);
}

describe("privacy page", () => {
  it("is open to visitors who are not signed in", async () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/privacy");

    expect(screen.getByRole("heading", { name: "Privacy", level: 1 })).toBeTruthy();
  });

  it("warns that questions go to Gemini's free tier, where Google may read them", () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/privacy");

    const warning = screen.getByText(/Google may use free-tier questions and answers/);
    expect(warning.textContent).toContain("people at Google may read them");
    expect(warning.textContent).toContain("cannot recall what Google already received");
    expect(warning.textContent).toContain("Do not put personal, confidential, or sensitive information");
    expect(within(warning).getByRole("link", { name: "Gemini API terms" }).getAttribute("href")).toBe(
      GEMINI_TERMS_URL,
    );
  });

  it("lists each kind of stored data with how long it is kept", () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/privacy");

    const rows = tableRows("What");
    expect(rows).toHaveLength(4);
    expect(rows[0]).toMatch(/GitHub user ID.*Until you delete your account/);
    expect(rows[1]).toMatch(/linked repositories.*Until you remove the repository/);
    expect(rows[2]).toMatch(/each question you ask.*90 days after you asked it/);
    expect(rows[3]).toMatch(/Daily usage counts.*30 days/);
  });

  it("says what is never stored", () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/privacy");

    expect(screen.getByText("your email address or GitHub password")).toBeTruthy();
    expect(screen.getByText(/your GitHub access token: it is used once/)).toBeTruthy();
    expect(screen.getByText("your searches: results are shown and not kept")).toBeTruthy();
  });

  it("names both cookies and says there is no tracking", () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/privacy");

    const rows = tableRows("Cookie");
    expect(rows.map((row) => row.split(" ")[0])).toEqual(["sessionKeeps", "sign_in_attemptProtects"]);
    expect(screen.getByText(/No analytics, advertising, or tracking/)).toBeTruthy();
  });

  it("links to deleting the account and shows the contact placeholder", () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/privacy");

    expect(screen.getAllByRole("link", { name: "account page" })[0].getAttribute("href")).toBe(
      "/account",
    );
    expect(
      screen.getByText(/will be listed here before anyone other than the developer can sign in/),
    ).toBeTruthy();
  });
});

describe("links to the privacy page", () => {
  it("are in the footer of every page", () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/no-such-page");

    const footer = screen.getByRole("contentinfo");
    expect(within(footer).getByRole("link", { name: "Privacy" }).getAttribute("href")).toBe(
      "/privacy",
    );
  });

  it("are beside the sign-in button on the home page", async () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/");

    expect(
      (await screen.findByRole("link", { name: "what is stored and who receives it" })).getAttribute(
        "href",
      ),
    ).toBe("/privacy");
  });

  it("come with a Gemini reminder on the question form", async () => {
    installFakeApi({
      "GET /api/me": SIGNED_IN,
      "GET /api/conversations/conversation-1": jsonReply(200, {
        ...conversation(),
        messages: [message("m1", "How does Flask sign the session cookie?")],
      }),
    });

    renderApp("/conversations/conversation-1");

    expect(await screen.findByText(/Questions are sent to Google's Gemini on its free tier/)).toBeTruthy();
  });

  it("are in the account deletion section", async () => {
    installFakeApi({ "GET /api/me": SIGNED_IN, "GET /api/me/usage": USAGE });

    renderApp("/account");

    expect((await screen.findByRole("link", { name: "privacy page" })).getAttribute("href")).toBe(
      "/privacy",
    );
  });
});
