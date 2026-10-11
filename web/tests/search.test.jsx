import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SIGNED_IN } from "./support/accounts.js";
import { searchReply, searchResult } from "./support/answers.js";
import { installFakeApi, jsonReply } from "./support/fakeApi.js";
import { renderApp } from "./support/renderApp.jsx";
import { readyLink } from "./support/repositories.js";

function searchApi(searchRoute) {
  return installFakeApi({
    "GET /api/me": SIGNED_IN,
    "GET /api/repositories/link-flask": jsonReply(200, readyLink()),
    "POST /api/repositories/link-flask/search": searchRoute,
  });
}

async function search(query) {
  await userEvent.type(await screen.findByLabelText("Search for"), query);
  await userEvent.click(screen.getByRole("button", { name: "Search" }));
}

describe("search page", () => {
  it("sends the query with the chosen options and shows ranked results with the license", async () => {
    const { calls } = searchApi(
      jsonReply(
        200,
        searchReply([
          searchResult(1),
          searchResult(2, {
            qualified_name: "test_session_expiration",
            kind: "function",
            file_path: "tests/test_basic.py",
            is_test_file: true,
          }),
        ]),
      ),
    );
    renderApp("/repositories/link-flask/search");
    await screen.findByLabelText("Search for");
    await userEvent.click(screen.getByLabelText("Leave out test files"));
    await userEvent.selectOptions(screen.getByRole("combobox"), "20");

    await search(" sign the session cookie ");

    const first = await screen.findByRole("link", {
      name: "1. SecureCookieSessionInterface.get_signing_serializer (method) · src/flask/sessions.py · lines 303–320",
    });
    expect(first.getAttribute("href")).toBe(searchResult(1).link);
    expect(screen.getByText(/test_session_expiration \(function\).*· test file$/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Read the license (LICENSE.txt)" })).toBeTruthy();
    const figure = first.closest("figure");
    expect(within(figure).getByText("6 more lines", { exact: false })).toBeTruthy();
    const body = JSON.parse(
      calls.find((call) => call.key === "POST /api/repositories/link-flask/search").options.body,
    );
    expect(body).toEqual({ query: "sign the session cookie", limit: 20, exclude_tests: true });
  });

  it("uses ten results and keeps test files by default", async () => {
    const { calls } = searchApi(jsonReply(200, searchReply([])));
    renderApp("/repositories/link-flask/search");

    await search("nothing like this");

    expect(await screen.findByText("No code matched.")).toBeTruthy();
    const body = JSON.parse(
      calls.find((call) => call.key === "POST /api/repositories/link-flask/search").options.body,
    );
    expect(body).toEqual({ query: "nothing like this", limit: 10, exclude_tests: false });
  });

  it("shows a refusal with its wait", async () => {
    searchApi(
      jsonReply(
        429,
        { error: "You have used today's 100 searches; the limit resets at midnight Pacific time" },
        { "Retry-After": "18000" },
      ),
    );
    renderApp("/repositories/link-flask/search");

    await search("session");

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Could not search: You have used today's 100 searches; the limit resets at midnight Pacific time (in about 5 hours).",
    );
  });
});
