import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import App from "../src/App.jsx";

function renderAt(path) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App", () => {
  it("shows the home page at the root", () => {
    renderAt("/");

    expect(
      screen.getByRole("heading", { name: "Ask questions about a Python repository" }),
    ).toBeTruthy();
  });

  it("links the site name to the home page", () => {
    renderAt("/");

    const siteLink = screen.getByRole("link", { name: "RepositoryCite" });

    expect(siteLink.getAttribute("href")).toBe("/");
  });

  it("shows a not found page for an unknown path", () => {
    renderAt("/no-such-page");

    expect(screen.getByRole("heading", { name: "Page not found" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to the home page" }).getAttribute("href")).toBe("/");
  });
});
