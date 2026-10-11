import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Timing } from "../src/timing.js";
import { MAX_QUESTION_CHARACTERS } from "../src/components/AskForm.jsx";
import { NO_CONTENT, SIGNED_IN, SIGNED_OUT } from "./support/accounts.js";
import { conversation, foundReply, message, notFoundReply } from "./support/answers.js";
import { installFakeApi, jsonReply } from "./support/fakeApi.js";
import { renderApp } from "./support/renderApp.jsx";
import { indexingLink, readyLink, replies } from "./support/repositories.js";

const FIRST_QUESTION = "How does Flask sign the session cookie?";

function conversationApi(extraRoutes = {}) {
  return installFakeApi({
    "GET /api/me": SIGNED_IN,
    "GET /api/conversations/conversation-1": jsonReply(200, {
      ...conversation(),
      messages: [message("m1", FIRST_QUESTION)],
    }),
    ...extraRoutes,
  });
}

function bodyOf(calls, key) {
  return JSON.parse(calls.find((call) => call.key === key).options.body);
}

async function ask(question, { excludeTests = false } = {}) {
  await userEvent.type(await screen.findByLabelText("Your question"), question);
  if (excludeTests) {
    await userEvent.click(screen.getByLabelText("Leave out test files"));
  }
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
}

describe("conversation page", () => {
  it("shows the title, repository, and every message with its answer", async () => {
    conversationApi();

    renderApp("/conversations/conversation-1");

    expect(await screen.findByRole("heading", { name: FIRST_QUESTION })).toBeTruthy();
    expect(screen.getByText("About pallets/flask 3.1.3")).toBeTruthy();
    expect(screen.getByText(FIRST_QUESTION, { selector: ".message-question" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Read the license (LICENSE.txt)" })).toBeTruthy();
  });

  it("asks a question and adds the answer below the others", async () => {
    const { calls } = conversationApi({
      "POST /api/conversations/conversation-1/messages": jsonReply(
        201,
        message("m2", "Where is url_for defined?", notFoundReply(), { exclude_tests: true }),
      ),
    });
    renderApp("/conversations/conversation-1");

    await ask("  Where is url_for defined?  ", { excludeTests: true });

    expect(await screen.findByText("Where is url_for defined?")).toBeTruthy();
    expect(screen.getByText("· test files left out")).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "Read the license (LICENSE.txt)" })).toHaveLength(2);
    expect(bodyOf(calls, "POST /api/conversations/conversation-1/messages")).toEqual({
      question: "Where is url_for defined?",
      exclude_tests: true,
    });
    expect(screen.getByLabelText("Your question").value).toBe("");
  });

  it("says it is answering while waiting and keeps the form closed meanwhile", async () => {
    conversationApi({
      "POST /api/conversations/conversation-1/messages": () => new Promise(() => {}),
    });
    renderApp("/conversations/conversation-1");

    await ask("Where is url_for defined?");

    expect(await screen.findByText(/this usually takes a few seconds/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Answering…" }).disabled).toBe(true);
    expect(screen.getByLabelText("Your question").disabled).toBe(true);
  });

  it("keeps the question and shows the refusal with its wait", async () => {
    conversationApi({
      "POST /api/conversations/conversation-1/messages": jsonReply(
        429,
        { error: "You are asking questions quickly; wait a moment before the next one" },
        { "Retry-After": "40" },
      ),
    });
    renderApp("/conversations/conversation-1");

    await ask("Where is url_for defined?");

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Could not answer: You are asking questions quickly; wait a moment before the next one (in less than a minute).",
    );
    expect(screen.getByLabelText("Your question").value).toBe("Where is url_for defined?");
    expect(performance.getEntriesByName(Timing.SHOWN_ANSWER, "measure")).toHaveLength(0);
  });

  it("times a question from sending it until its answer is shown", async () => {
    conversationApi({
      "POST /api/conversations/conversation-1/messages": jsonReply(
        201,
        message("m2", "Where is url_for defined?", notFoundReply()),
      ),
    });
    renderApp("/conversations/conversation-1");
    await screen.findByRole("heading", { name: FIRST_QUESTION });
    expect(performance.getEntriesByName(Timing.SHOWN_ANSWER, "measure")).toHaveLength(0);

    await ask("Where is url_for defined?");

    await screen.findByText("Where is url_for defined?");
    await waitFor(() =>
      expect(performance.getEntriesByName(Timing.SHOWN_ANSWER, "measure")).toHaveLength(1),
    );
  });

  it("refuses a question over the length limit before sending it", async () => {
    conversationApi();
    renderApp("/conversations/conversation-1");
    const textarea = await screen.findByLabelText("Your question");

    await userEvent.click(textarea);
    await userEvent.paste("a".repeat(MAX_QUESTION_CHARACTERS + 1));

    expect(screen.getByText(`${MAX_QUESTION_CHARACTERS + 1} / ${MAX_QUESTION_CHARACTERS}`)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Ask" }).disabled).toBe(true);
  });

  it("says when the conversation does not exist", async () => {
    installFakeApi({
      "GET /api/me": SIGNED_IN,
      "GET /api/conversations/missing": jsonReply(404, { error: "No such conversation" }),
    });

    renderApp("/conversations/missing");

    expect(await screen.findByRole("heading", { name: "Conversation not found" })).toBeTruthy();
  });

  it("deletes the conversation after confirming and goes back to the list", async () => {
    const { calls } = conversationApi({
      "DELETE /api/conversations/conversation-1": NO_CONTENT,
      "GET /api/conversations": jsonReply(200, { conversations: [] }),
    });
    renderApp("/conversations/conversation-1");

    await userEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(screen.getByText("Delete this conversation and all its answers?")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect(await screen.findByText(/No conversations yet/)).toBeTruthy();
    expect(calls.filter((call) => call.key === "DELETE /api/conversations/conversation-1")).toHaveLength(1);
  });
});

describe("conversations list", () => {
  it("lists conversations with their repositories, naming untitled ones", async () => {
    installFakeApi({
      "GET /api/me": SIGNED_IN,
      "GET /api/conversations": jsonReply(200, {
        conversations: [conversation(), conversation({ id: "conversation-2", title: null })],
      }),
    });

    renderApp("/conversations");

    const link = await screen.findByRole("link", { name: FIRST_QUESTION });
    expect(link.getAttribute("href")).toBe("/conversations/conversation-1");
    expect(screen.getByRole("link", { name: "New conversation" }).getAttribute("href")).toBe(
      "/conversations/conversation-2",
    );
    expect(screen.getByText(/deleted 90 days after they were last used/)).toBeTruthy();
  });

  it("asks a signed-out visitor to sign in", async () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/conversations");

    expect(await screen.findByText("Sign in to see your conversations.")).toBeTruthy();
  });
});

describe("starting a conversation", () => {
  it("offers asking and searching on a ready repository's card", async () => {
    installFakeApi({
      "GET /api/me": SIGNED_IN,
      "GET /api/repositories": jsonReply(200, { repositories: [readyLink(), indexingLink()] }),
    });

    renderApp("/repositories");

    const askLinks = await screen.findAllByRole("link", { name: "Ask a question" });
    expect(askLinks.map((link) => link.getAttribute("href"))).toEqual([
      "/repositories/link-flask/ask",
    ]);
    expect(screen.getByRole("link", { name: "Search the code" }).getAttribute("href")).toBe(
      "/repositories/link-flask/search",
    );
  });

  it("creates the conversation only when the first question is sent, then opens it", async () => {
    const { calls } = installFakeApi({
      "GET /api/me": SIGNED_IN,
      "GET /api/repositories/link-flask": jsonReply(200, readyLink()),
      "POST /api/conversations": jsonReply(201, conversation({ title: null })),
      "POST /api/conversations/conversation-1/messages": jsonReply(
        201,
        message("m1", FIRST_QUESTION),
      ),
      "GET /api/conversations/conversation-1": jsonReply(200, {
        ...conversation(),
        messages: [message("m1", FIRST_QUESTION)],
      }),
    });
    renderApp("/repositories/link-flask/ask");

    expect(await screen.findByRole("heading", { name: "Ask about pallets/flask" })).toBeTruthy();
    expect(calls.some((call) => call.key === "POST /api/conversations")).toBe(false);
    await ask(FIRST_QUESTION);

    expect(await screen.findByRole("heading", { name: FIRST_QUESTION })).toBeTruthy();
    expect(bodyOf(calls, "POST /api/conversations")).toEqual({ link_id: "link-flask" });
    await waitFor(() =>
      expect(performance.getEntriesByName(Timing.SHOWN_ANSWER, "measure")).toHaveLength(1),
    );
  });

  it("reuses the new conversation when the first question has to be asked again", async () => {
    const { calls } = installFakeApi({
      "GET /api/me": SIGNED_IN,
      "GET /api/repositories/link-flask": jsonReply(200, readyLink()),
      "POST /api/conversations": jsonReply(201, conversation({ title: null })),
      "POST /api/conversations/conversation-1/messages": replies(
        jsonReply(503, { error: "Gemini is unavailable for now; try again shortly" }, { "Retry-After": "30" }),
        jsonReply(201, message("m1", FIRST_QUESTION, foundReply())),
      ),
      "GET /api/conversations/conversation-1": jsonReply(200, {
        ...conversation(),
        messages: [message("m1", FIRST_QUESTION)],
      }),
    });
    renderApp("/repositories/link-flask/ask");

    await ask(FIRST_QUESTION);
    expect((await screen.findByRole("alert")).textContent).toContain("Gemini is unavailable");
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));

    expect(await screen.findByRole("heading", { name: FIRST_QUESTION })).toBeTruthy();
    expect(calls.filter((call) => call.key === "POST /api/conversations")).toHaveLength(1);
  });

  it("explains that a repository still indexing cannot take questions", async () => {
    installFakeApi({
      "GET /api/me": SIGNED_IN,
      "GET /api/repositories/link-itsdangerous": jsonReply(200, indexingLink()),
    });

    renderApp("/repositories/link-itsdangerous/ask");

    expect(await screen.findByText(/This repository is not ready yet/)).toBeTruthy();
    expect(screen.queryByLabelText("Your question")).toBeNull();
  });

  it("says when the repository does not exist", async () => {
    installFakeApi({
      "GET /api/me": SIGNED_IN,
      "GET /api/repositories/nope": jsonReply(404, { error: "No such repository" }),
    });

    renderApp("/repositories/nope/ask");

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Repository not found" })).toBeTruthy(),
    );
  });
});
