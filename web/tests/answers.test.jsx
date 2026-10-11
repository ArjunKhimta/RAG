import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  citationTitle,
  lineRangeText,
  snippetAnchor,
  snippetIndexFor,
} from "../src/answers/citations.js";
import AnswerView from "../src/components/AnswerView.jsx";
import { foundReply, notFoundReply, REPOSITORY_BLOCK } from "./support/answers.js";

function renderAnswer(reply) {
  return render(<AnswerView messageId="m1" reply={reply} />);
}

function licenseLink() {
  return screen.getByRole("link", { name: "Read the license (LICENSE.txt)" });
}

describe("citation helpers", () => {
  const reply = foundReply();

  it("finds the snippet a citation points to by source and lines", () => {
    expect(snippetIndexFor({ source: 1, start_line: 315, end_line: 320 }, reply.snippets)).toBe(1);
    expect(snippetIndexFor({ source: 1, start_line: 1, end_line: 2 }, reply.snippets)).toBe(-1);
  });

  it("describes a citation by file and lines", () => {
    expect(citationTitle({ source: 1, start_line: 303, end_line: 314 }, reply.sources)).toBe(
      "src/flask/sessions.py, lines 303–314",
    );
    expect(citationTitle({ source: 9, start_line: 5, end_line: 5 }, reply.sources)).toBe("line 5");
  });

  it("names a single line and a range differently", () => {
    expect(lineRangeText(7, 7)).toBe("line 7");
    expect(lineRangeText(7, 9)).toBe("lines 7–9");
  });
});

describe("answer view", () => {
  it("puts each sentence's citation markers after it, linked to the cited snippet", () => {
    renderAnswer(foundReply());

    const markers = screen.getAllByRole("link", { name: /^\[\d\]$/ });
    expect(markers.map((marker) => marker.textContent)).toEqual(["[1]", "[1]", "[2]"]);
    expect(markers.map((marker) => marker.getAttribute("href"))).toEqual([
      `#${snippetAnchor("m1", 0)}`,
      `#${snippetAnchor("m1", 1)}`,
      `#${snippetAnchor("m1", 2)}`,
    ]);
    expect(markers[0].getAttribute("title")).toBe("src/flask/sessions.py, lines 303–314");
    expect(screen.getByText(/Sessions are stored in a cookie\./)).toBeTruthy();
  });

  it("shows each snippet with its real line numbers and a link to those lines", () => {
    const { container } = renderAnswer(foundReply());

    const first = container.querySelector(`#${snippetAnchor("m1", 0)}`);
    const numbers = [...first.querySelectorAll(".line-number")].map((cell) => cell.textContent);
    expect(numbers).toEqual(["303", "304"]);
    expect(first.textContent).toContain("def get_signing_serializer");
    const heading = within(first).getByRole("link", {
      name: "[1] src/flask/sessions.py · SecureCookieSessionInterface.get_signing_serializer · lines 303–314",
    });
    expect(heading.getAttribute("href")).toBe(foundReply().snippets[0].link);
  });

  it("says how many lines are left out, linking to the rest", () => {
    const { container } = renderAnswer(foundReply());

    const second = container.querySelector(`#${snippetAnchor("m1", 1)}`);
    expect(second.textContent).toContain("3 more lines on GitHub");
  });

  it("shows a class outline without line numbers and says so", () => {
    const { container } = renderAnswer(foundReply());

    const outline = container.querySelector(`#${snippetAnchor("m1", 2)}`);
    const numbers = [...outline.querySelectorAll(".line-number")].map((cell) => cell.textContent);
    expect(numbers).toEqual(["", ""]);
    expect(outline.textContent).toContain("class outline, method bodies left out");
  });

  it("lists the sources the answer could draw on", () => {
    renderAnswer(foundReply());

    expect(screen.getByText("2 sources the answer could draw on")).toBeTruthy();
  });

  it("links the license file at the indexed commit on every answer", () => {
    renderAnswer(foundReply());

    expect(licenseLink().getAttribute("href")).toBe(REPOSITORY_BLOCK.license.link);
    expect(screen.getByText(/used under BSD-3-Clause/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "22d9247" }).getAttribute("href")).toBe(
      `https://github.com/pallets/flask/tree/${REPOSITORY_BLOCK.commit_id}`,
    );
  });

  it("links the license on an answer that found nothing, too", () => {
    renderAnswer(notFoundReply());

    expect(
      screen.getByText("No code in this repository version matched the question."),
    ).toBeTruthy();
    expect(screen.queryAllByRole("link", { name: /^\[\d\]$/ })).toHaveLength(0);
    expect(licenseLink().getAttribute("href")).toBe(REPOSITORY_BLOCK.license.link);
  });

  it("never links an address that is not GitHub, but still shows the text", () => {
    const reply = foundReply();
    reply.snippets[0] = { ...reply.snippets[0], link: "javascript:alert(1)" };
    reply.repository = {
      ...reply.repository,
      license: { ...reply.repository.license, link: "https://evil.example/LICENSE" },
    };

    const { container } = renderAnswer(reply);

    const first = container.querySelector(`#${snippetAnchor("m1", 0)}`);
    expect(within(first).queryByRole("link")).toBeNull();
    expect(first.textContent).toContain("get_signing_serializer");
    expect(screen.queryByRole("link", { name: /Read the license/ })).toBeNull();
    expect(screen.getByText(/License file link unavailable/)).toBeTruthy();
  });

  it("shows code as text, never as markup", () => {
    const reply = foundReply();
    reply.snippets[0] = {
      ...reply.snippets[0],
      lines: [{ number: 1, text: '<img src=x onerror="alert(1)">' }],
    };

    const { container } = renderAnswer(reply);

    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">');
  });
});
