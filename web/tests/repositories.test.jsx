import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POLL_INTERVAL_MILLISECONDS } from "../src/components/RepositoryCard.jsx";
import { NO_CONTENT, SIGNED_IN, SIGNED_OUT } from "./support/accounts.js";
import { installFakeApi, jsonReply } from "./support/fakeApi.js";
import { renderApp } from "./support/renderApp.jsx";
import {
  failedLink,
  indexingLink,
  readyLink,
  replies,
  waitingLink,
} from "./support/repositories.js";

function repositoriesApi(links, extraRoutes = {}) {
  return installFakeApi({
    "GET /api/me": SIGNED_IN,
    "GET /api/repositories": jsonReply(200, { repositories: links }),
    ...extraRoutes,
  });
}

async function cardFor(repository) {
  const link = await screen.findByRole("link", { name: repository });
  return link.closest("li");
}

async function advancePolling() {
  await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MILLISECONDS));
}

function callCount(calls, key) {
  return calls.filter((call) => call.key === key).length;
}

afterEach(() => {
  vi.useRealTimers();
});

describe("repositories page", () => {
  it("asks a signed-out visitor to sign in", async () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/repositories");

    expect(
      await screen.findByText("Sign in to add repositories and ask questions about them."),
    ).toBeTruthy();
  });

  it("is linked from the header once signed in", async () => {
    repositoriesApi([]);

    renderApp("/");

    expect((await screen.findByRole("link", { name: "Repositories" })).getAttribute("href")).toBe(
      "/repositories",
    );
  });

  it("says when there are no repositories yet", async () => {
    repositoriesApi([]);

    renderApp("/repositories");

    expect(await screen.findByText("You have not added any repositories yet.")).toBeTruthy();
  });

  it("explains when the list cannot be loaded", async () => {
    installFakeApi({
      "GET /api/me": SIGNED_IN,
      "GET /api/repositories": jsonReply(500, { error: "Something went wrong" }),
    });

    renderApp("/repositories");

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Could not load your repositories: Something went wrong",
    );
  });

  it("shows a ready repository with its commit and license links", async () => {
    repositoriesApi([readyLink()]);

    renderApp("/repositories");

    const card = await cardFor("pallets/flask");
    expect(within(card).getByText("Ready")).toBeTruthy();
    expect(within(card).getByRole("link", { name: "pallets/flask" }).getAttribute("href")).toBe(
      "https://github.com/pallets/flask",
    );
    expect(within(card).getByRole("link", { name: "22d9247" }).getAttribute("href")).toBe(
      "https://github.com/pallets/flask/tree/22d924701a6ae2e4cd01e9a15bbaf3946094af65",
    );
    expect(within(card).getByRole("link", { name: "BSD-3-Clause" }).getAttribute("href")).toBe(
      readyLink().indexed.license.link,
    );
    expect(within(card).queryByRole("button", { name: "Resume indexing" })).toBeNull();
  });

  it("shows a license name without a link when the link is not a GitHub address", async () => {
    const link = readyLink();
    link.indexed = {
      ...link.indexed,
      license: { ...link.indexed.license, link: "javascript:alert(1)" },
    };
    repositoriesApi([link]);

    renderApp("/repositories");

    const card = await cardFor("pallets/flask");
    expect(within(card).getByText(/BSD-3-Clause/)).toBeTruthy();
    expect(within(card).queryByRole("link", { name: "BSD-3-Clause" })).toBeNull();
  });

  it("says which files in other languages a ready repository left out", async () => {
    const link = readyLink();
    link.indexed = {
      ...link.indexed,
      other_language_files: [
        { language: "C", files: 214 },
        { language: "C++", files: 1 },
      ],
    };
    repositoriesApi([link]);

    renderApp("/repositories");

    const card = await cardFor("pallets/flask");
    expect(
      within(card).getByText(
        "Only the Python code was indexed; this repository also has 214 C files and 1 C++ file.",
      ),
    ).toBeTruthy();
  });

  it("says nothing about other languages when there are none or none were counted", async () => {
    const counted = readyLink();
    counted.indexed = { ...counted.indexed, other_language_files: [] };
    repositoriesApi([counted, readyLink({ id: "link-jinja", repository: "pallets/jinja" })]);

    renderApp("/repositories");

    await cardFor("pallets/jinja");
    expect(screen.queryByText(/Only the Python code was indexed/)).toBeNull();
  });

  it("shows a repository with no Python code as a permanent failure", async () => {
    repositoriesApi([
      failedLink(
        "no_python_code",
        "someone/missing at 1.0.0 has no Python code to index (it has 40 JavaScript files); only Python is supported for now",
      ),
    ]);

    renderApp("/repositories");

    const card = await cardFor("someone/missing");
    expect(within(card).getByText(/has no Python code to index/)).toBeTruthy();
    expect(within(card).getByText(/Trying again would fail the same way/)).toBeTruthy();
    expect(within(card).queryByRole("button", { name: "Resume indexing" })).toBeNull();
  });

  it("ends the service's message with a full stop before saying a retry may work", async () => {
    repositoriesApi([
      failedLink(
        "github_rate_limited",
        "GitHub's limit for looking up repositories is used up for now; it resets within the hour",
      ),
    ]);

    renderApp("/repositories");

    const card = await cardFor("someone/missing");
    expect(
      within(card).getByText(
        "GitHub's limit for looking up repositories is used up for now; it resets within the hour. This may work if you try again.",
      ),
    ).toBeTruthy();
    expect(within(card).getByRole("button", { name: "Resume indexing" })).toBeTruthy();
  });

  it("shows a permanent failure without a resume button", async () => {
    repositoriesApi([failedLink("not_found", "This repository does not exist on GitHub")]);

    renderApp("/repositories");

    const card = await cardFor("someone/missing");
    expect(within(card).getByText("Failed")).toBeTruthy();
    expect(within(card).getByText(/This repository does not exist on GitHub/)).toBeTruthy();
    expect(within(card).getByText(/Trying again would fail the same way/)).toBeTruthy();
    expect(within(card).queryByRole("button", { name: "Resume indexing" })).toBeNull();
  });

  it("offers to resume a paused repository only after the quota resets", async () => {
    const future = new Date(Date.now() + 3_600_000).toISOString();
    const past = new Date(Date.now() - 1000).toISOString();
    repositoriesApi([
      waitingLink(future),
      waitingLink(past, { id: "link-past", repository: "pallets/jinja" }),
    ]);

    renderApp("/repositories");

    const waitingCard = await cardFor("pallets/click");
    expect(within(waitingCard).getByText(/after 800 of 1200 pieces of code/)).toBeTruthy();
    expect(within(waitingCard).getByText(/You can resume after/)).toBeTruthy();
    expect(within(waitingCard).queryByRole("button", { name: "Resume indexing" })).toBeNull();
    const readyToResume = await cardFor("pallets/jinja");
    expect(within(readyToResume).getByRole("button", { name: "Resume indexing" })).toBeTruthy();
  });

  it("shows the resume button once the reset time passes while the page is open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const soon = new Date(Date.now() + 20_000).toISOString();
    repositoriesApi([waitingLink(soon)]);
    renderApp("/repositories");
    const card = await cardFor("pallets/click");
    expect(within(card).queryByRole("button", { name: "Resume indexing" })).toBeNull();

    await act(() => vi.advanceTimersByTimeAsync(30_000));

    expect(within(card).getByRole("button", { name: "Resume indexing" })).toBeTruthy();
  });
});

describe("adding a repository", () => {
  it("keeps the button disabled until both fields are filled", async () => {
    repositoriesApi([]);
    renderApp("/repositories");
    const button = await screen.findByRole("button", { name: "Add repository" });

    expect(button.disabled).toBe(true);
    await userEvent.type(screen.getByLabelText("Repository"), "pallets/flask");
    expect(button.disabled).toBe(true);
    await userEvent.type(screen.getByLabelText("Version"), "   ");
    expect(button.disabled).toBe(true);
    await userEvent.type(screen.getByLabelText("Version"), "3.1.3");
    expect(button.disabled).toBe(false);
  });

  it("sends the name taken from a pasted GitHub address and shows the new link", async () => {
    const { calls } = repositoriesApi([], {
      "POST /api/repositories": jsonReply(202, indexingLink()),
    });
    renderApp("/repositories");

    await userEvent.type(
      await screen.findByLabelText("Repository"),
      "https://github.com/pallets/itsdangerous",
    );
    await userEvent.type(screen.getByLabelText("Version"), " 2.2.0 ");
    await userEvent.click(screen.getByRole("button", { name: "Add repository" }));

    const card = await cardFor("pallets/itsdangerous");
    expect(within(card).getByText("Indexing")).toBeTruthy();
    expect(within(card).getByText("Downloading the repository from GitHub…")).toBeTruthy();
    const post = calls.find((call) => call.key === "POST /api/repositories");
    expect(JSON.parse(post.options.body)).toEqual({
      repository: "pallets/itsdangerous",
      version: "2.2.0",
    });
    expect(screen.getByLabelText("Repository").value).toBe("");
  });

  it("does not list a repository twice when it was already linked", async () => {
    repositoriesApi([readyLink()], { "POST /api/repositories": jsonReply(200, readyLink()) });
    renderApp("/repositories");

    await userEvent.type(await screen.findByLabelText("Repository"), "Pallets/Flask");
    await userEvent.type(screen.getByLabelText("Version"), "3.1.3");
    await userEvent.click(screen.getByRole("button", { name: "Add repository" }));

    await screen.findByRole("link", { name: "pallets/flask" });
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  it("shows the server's refusal with how long to wait", async () => {
    repositoriesApi([], {
      "POST /api/repositories": jsonReply(
        429,
        { error: "You have used today's 2 new indexing requests; the limit resets at midnight Pacific time" },
        { "Retry-After": "7200" },
      ),
    });
    renderApp("/repositories");

    await userEvent.type(await screen.findByLabelText("Repository"), "pallets/click");
    await userEvent.type(screen.getByLabelText("Version"), "8.3.0");
    await userEvent.click(screen.getByRole("button", { name: "Add repository" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Could not add the repository: You have used today's 2 new indexing requests; the limit resets at midnight Pacific time (in about 2 hours).",
    );
    expect(screen.getByLabelText("Repository").value).toBe("pallets/click");
  });
});

describe("indexing progress", () => {
  it("polls while indexing, shows embedding progress, and stops once ready", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { calls } = repositoriesApi([indexingLink()], {
      "GET /api/repositories/link-itsdangerous": replies(
        jsonReply(200, indexingLink({ stage: "embedding", progress: { embedded: 80, to_embed: 158 } })),
        jsonReply(200, indexingLink({ status: "ready", stage: null, indexed: readyLink().indexed })),
      ),
    });
    renderApp("/repositories");
    const card = await cardFor("pallets/itsdangerous");

    await advancePolling();
    expect(within(card).getByText("80 of 158 pieces of code embedded")).toBeTruthy();
    expect(within(card).getByRole("progressbar").getAttribute("value")).toBe("80");

    await advancePolling();
    expect(within(card).getByText("Ready")).toBeTruthy();

    await advancePolling();
    await advancePolling();
    expect(callCount(calls, "GET /api/repositories/link-itsdangerous")).toBe(2);
  });

  it("keeps polling after a failed refresh and says so", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    repositoriesApi([indexingLink()], {
      "GET /api/repositories/link-itsdangerous": replies(
        jsonReply(503, { error: "The retrieval service is busy" }),
        jsonReply(200, indexingLink({ stage: "chunking" })),
      ),
    });
    renderApp("/repositories");
    const card = await cardFor("pallets/itsdangerous");

    await advancePolling();
    expect(within(card).getByRole("alert").textContent).toContain(
      "Could not refresh the progress: The retrieval service is busy",
    );

    await advancePolling();
    expect(within(card).queryByRole("alert")).toBeNull();
    expect(within(card).getByText("Splitting the code into functions and classes…")).toBeTruthy();
  });

  it("drops a link that disappeared while indexing", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    repositoriesApi([indexingLink()], {
      "GET /api/repositories/link-itsdangerous": jsonReply(404, { error: "No such repository" }),
    });
    renderApp("/repositories");
    await cardFor("pallets/itsdangerous");

    await advancePolling();

    expect(screen.queryByRole("link", { name: "pallets/itsdangerous" })).toBeNull();
  });
});

describe("resuming and removing", () => {
  it("resumes a retryable failure and starts showing progress", async () => {
    const failed = failedLink("clone_failed", "Could not download the repository");
    repositoriesApi([failed], {
      "POST /api/repositories/link-clone_failed/resume": jsonReply(
        202,
        { ...failed, status: "indexing", stage: "cloning", error: null },
      ),
    });
    renderApp("/repositories");
    const card = await cardFor("someone/missing");

    await userEvent.click(within(card).getByRole("button", { name: "Resume indexing" }));

    expect(await within(card).findByText("Indexing")).toBeTruthy();
    expect(within(card).queryByRole("button", { name: "Resume indexing" })).toBeNull();
  });

  it("shows the server's reason when resuming is refused", async () => {
    repositoriesApi([readyLink({ status: "interrupted", indexed: null })], {
      "POST /api/repositories/link-flask/resume": jsonReply(
        503,
        { error: "Another repository is being indexed; try again shortly" },
        { "Retry-After": "60" },
      ),
    });
    renderApp("/repositories");
    const card = await cardFor("pallets/flask");

    await userEvent.click(within(card).getByRole("button", { name: "Resume indexing" }));

    expect((await within(card).findByRole("alert")).textContent).toBe(
      "Another repository is being indexed; try again shortly (in about 1 minute).",
    );
  });

  it("removes a repository only after confirming", async () => {
    const { calls } = repositoriesApi([readyLink()], {
      "DELETE /api/repositories/link-flask": NO_CONTENT,
    });
    renderApp("/repositories");
    const card = await cardFor("pallets/flask");

    await userEvent.click(within(card).getByRole("button", { name: "Remove" }));
    expect(within(card).getByText(/Its index stays for others/)).toBeTruthy();
    expect(callCount(calls, "DELETE /api/repositories/link-flask")).toBe(0);

    await userEvent.click(within(card).getByRole("button", { name: "Remove" }));

    expect(await screen.findByText("You have not added any repositories yet.")).toBeTruthy();
    expect(callCount(calls, "DELETE /api/repositories/link-flask")).toBe(1);
  });

  it("keeps the repository when removal is cancelled", async () => {
    const { calls } = repositoriesApi([readyLink()]);
    renderApp("/repositories");
    const card = await cardFor("pallets/flask");

    await userEvent.click(within(card).getByRole("button", { name: "Remove" }));
    await userEvent.click(within(card).getByRole("button", { name: "Cancel" }));

    expect(within(card).queryByText(/Its index stays for others/)).toBeNull();
    expect(calls.some((call) => call.key.startsWith("DELETE"))).toBe(false);
  });
});

describe("refreshing from GitHub", () => {
  const MOVED_COMMIT = "d086db856be187255b8ec61ef409357393020f32";

  function movedIndexed() {
    const indexed = readyLink().indexed;
    return { ...indexed, commit_id: MOVED_COMMIT };
  }

  function refreshingLink(refresh) {
    return readyLink({
      refresh: {
        status: "running",
        stage: null,
        progress: null,
        error: null,
        resume_after: null,
        ...refresh,
      },
    });
  }

  it("says when GitHub still has the indexed commit", async () => {
    repositoriesApi([readyLink()], {
      "POST /api/repositories/link-flask/refresh": jsonReply(200, readyLink({ refresh: null })),
    });
    renderApp("/repositories");
    const card = await cardFor("pallets/flask");

    await userEvent.click(within(card).getByRole("button", { name: "Refresh from GitHub" }));

    expect((await within(card).findByRole("status")).textContent).toBe(
      "Up to date: GitHub still has 3.1.3 at commit 22d9247.",
    );
  });

  it("follows a refresh while the old commit stays usable, then shows the new commit", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { calls } = repositoriesApi([readyLink()], {
      "POST /api/repositories/link-flask/refresh": jsonReply(202, refreshingLink({})),
      "GET /api/repositories/link-flask": replies(
        jsonReply(
          200,
          refreshingLink({ stage: "embedding", progress: { embedded: 10, to_embed: 21 } }),
        ),
        jsonReply(200, readyLink({ indexed: movedIndexed(), refresh: null })),
      ),
    });
    renderApp("/repositories");
    const card = await cardFor("pallets/flask");

    await userEvent.click(within(card).getByRole("button", { name: "Refresh from GitHub" }));
    expect(await within(card).findByText(/Refreshing from GitHub/)).toBeTruthy();
    expect(within(card).getByRole("link", { name: "Ask a question" })).toBeTruthy();
    expect(within(card).queryByRole("button", { name: "Refresh from GitHub" })).toBeNull();

    await advancePolling();
    expect(within(card).getByText("10 of 21 pieces of code embedded")).toBeTruthy();

    await advancePolling();
    expect(within(card).getByRole("status").textContent).toBe(
      "Refreshed: now indexed at commit d086db8.",
    );
    expect(within(card).getByRole("link", { name: "d086db8" })).toBeTruthy();

    await advancePolling();
    expect(callCount(calls, "GET /api/repositories/link-flask")).toBe(2);
  });

  it("explains a failed refresh and offers to try again", async () => {
    const failed = refreshingLink({
      status: "failed",
      error: { code: "clone_failed", message: "The version could not be downloaded" },
    });
    repositoriesApi([failed]);
    renderApp("/repositories");
    const card = await cardFor("pallets/flask");

    expect(
      within(card).getByText(
        "The refresh did not finish: The version could not be downloaded. " +
          "The commit above is still searchable.",
      ),
    ).toBeTruthy();
    expect(within(card).getByRole("button", { name: "Refresh from GitHub" })).toBeTruthy();
    expect(within(card).getByText("Ready")).toBeTruthy();
  });

  it("waits for the quota before offering to refresh again", async () => {
    const waiting = refreshingLink({
      status: "waiting_for_quota",
      resume_after: "2999-01-01T08:00:00.000Z",
    });
    repositoriesApi([waiting]);
    renderApp("/repositories");
    const card = await cardFor("pallets/flask");

    expect(within(card).getByText(/ran out while refreshing/)).toBeTruthy();
    expect(within(card).queryByRole("button", { name: "Refresh from GitHub" })).toBeNull();
  });

  it("shows the server's reason when a refresh is refused", async () => {
    repositoriesApi([readyLink()], {
      "POST /api/repositories/link-flask/refresh": jsonReply(409, {
        error: "This tag or branch no longer exists on GitHub, so it cannot be refreshed",
      }),
    });
    renderApp("/repositories");
    const card = await cardFor("pallets/flask");

    await userEvent.click(within(card).getByRole("button", { name: "Refresh from GitHub" }));

    expect((await within(card).findByRole("alert")).textContent).toBe(
      "This tag or branch no longer exists on GitHub, so it cannot be refreshed",
    );
  });

  it("is not offered before a repository is ready", async () => {
    repositoriesApi([failedLink("clone_failed", "Could not download"), indexingLink()]);
    renderApp("/repositories");

    await cardFor("someone/missing");

    expect(screen.queryByRole("button", { name: "Refresh from GitHub" })).toBeNull();
  });
});
