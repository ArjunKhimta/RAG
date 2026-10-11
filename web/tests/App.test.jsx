import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SIGNED_OUT } from "./support/accounts.js";
import { installFakeApi } from "./support/fakeApi.js";
import { renderApp } from "./support/renderApp.jsx";

describe("App", () => {
  it("shows the home page at the root", async () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/");

    expect(
      screen.getByRole("heading", { name: "Ask questions about a Python repository" }),
    ).toBeTruthy();
  });

  it("links the site name to the home page", () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/");

    expect(screen.getByRole("link", { name: "RepositoryCite" }).getAttribute("href")).toBe("/");
  });

  it("shows a not found page for an unknown path", () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/no-such-page");

    expect(screen.getByRole("heading", { name: "Page not found" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to the home page" }).getAttribute("href")).toBe(
      "/",
    );
  });
});
