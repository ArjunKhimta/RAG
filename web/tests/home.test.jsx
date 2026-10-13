import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import AnswerPipeline from "../src/home/AnswerPipeline.jsx";
import AnswerReplay from "../src/home/AnswerReplay.jsx";
import { EXAMPLE_ANSWER } from "../src/home/exampleAnswer.js";
import MeasuredResults from "../src/home/MeasuredResults.jsx";
import { SIGNED_OUT } from "./support/accounts.js";
import { installFakeApi } from "./support/fakeApi.js";
import { renderApp } from "./support/renderApp.jsx";

function citedLineNumbers(container) {
  return [...container.querySelectorAll(".code-line.is-cited .line-number")].map(
    (cell) => cell.textContent,
  );
}

describe("answer replay", () => {
  it("starts on the first sentence and lights up every line it cites", () => {
    const { container } = render(<AnswerReplay />);

    const first = screen.getByRole("button", { name: /Flask gets a signing serializer/ });
    expect(first.getAttribute("aria-pressed")).toBe("true");
    expect(citedLineNumbers(container)).toHaveLength(19);
  });

  it("lights up only the chosen sentence's lines when another is chosen", async () => {
    const { container } = render(<AnswerReplay />);

    await userEvent.click(screen.getByRole("button", { name: /It adds any fallback keys/ }));

    expect(citedLineNumbers(container)).toEqual(["307", "308", "309", "310", "311", "312"]);
    expect(
      screen.getByRole("button", { name: /Flask gets a signing serializer/ }).getAttribute(
        "aria-pressed",
      ),
    ).toBe("false");
  });

  it("shows the real code with its real line numbers, as text", () => {
    const { container } = render(<AnswerReplay />);

    const numbers = [...container.querySelectorAll(".line-number")].map((cell) => cell.textContent);
    expect(numbers[0]).toBe("303");
    expect(numbers.at(-1)).toBe("321");
    expect(container.textContent).toContain('app.config["SECRET_KEY_FALLBACKS"]');
  });

  it("links the license file and the cited lines at the indexed commit", () => {
    render(<AnswerReplay />);

    expect(
      screen.getByRole("link", { name: "Read the license (LICENSE.txt)" }).getAttribute("href"),
    ).toBe(EXAMPLE_ANSWER.licenseLink);
    expect(
      screen.getByRole("link", { name: "Open these lines on GitHub" }).getAttribute("href"),
    ).toContain(`/blob/${EXAMPLE_ANSWER.commitId}/src/flask/sessions.py#L303-L321`);
  });

  it("keeps only lines inside the cited definition", () => {
    const numbers = EXAMPLE_ANSWER.lines.map((line) => line.number);

    for (const sentence of EXAMPLE_ANSWER.sentences) {
      expect(sentence.startLine).toBeGreaterThanOrEqual(numbers[0]);
      expect(sentence.endLine).toBeLessThanOrEqual(numbers.at(-1));
    }
  });
});

describe("how an answer is made", () => {
  it("starts on the answer stage and shows another stage when chosen", async () => {
    render(<AnswerPipeline />);

    expect(screen.getByRole("button", { name: "answer" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText(/Gemini writes the answer as sentences/)).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "chunk" }));

    expect(screen.getByText(/Tree-sitter parses each Python file/)).toBeTruthy();
    expect(screen.getByText("1,009")).toBeTruthy();
    expect(screen.queryByText(/Gemini writes the answer as sentences/)).toBeNull();
  });

  it("names every timed stage of a question with its measured time", () => {
    render(<AnswerPipeline />);

    const key = screen.getByRole("list");
    expect(within(key).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "checks 32 ms",
      "embed question 37 ms",
      "vector search 73 ms",
      "answer model 2,224 ms",
    ]);
    expect(screen.getByText(/Where 2.4 s of a question goes/)).toBeTruthy();
  });
});

describe("measured results", () => {
  it("shows the live setup first and the expanded setup when chosen", async () => {
    render(<MeasuredResults />);

    expect(screen.getByText("28 / 47")).toBeTruthy();
    expect(screen.getByText("113 / 114")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "+ callers and callees" }));

    expect(screen.getByText("27 / 47")).toBeTruthy();
    expect(screen.getByText("2,977")).toBeTruthy();
    expect(screen.queryByText("28 / 47")).toBeNull();
    expect(screen.getByText(/so the site leaves it off/)).toBeTruthy();
  });

  it("says the sentences have not been checked by a person", () => {
    render(<MeasuredResults />);

    expect(screen.getByText(/have not yet been checked by a person/)).toBeTruthy();
  });
});

describe("home page", () => {
  it("shows the replay, the stages, and the results to a signed-out visitor", async () => {
    installFakeApi({ "GET /api/me": SIGNED_OUT });

    renderApp("/");

    expect(await screen.findAllByRole("link", { name: "Sign in with GitHub" })).toHaveLength(2);
    expect(screen.getByRole("region", { name: "A saved answer, replayed" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "How an answer is made" })).toBeTruthy();
    expect(screen.getByText(/uses retrieval-augmented generation \(RAG\)/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Measured, not claimed" })).toBeTruthy();
  });
});
