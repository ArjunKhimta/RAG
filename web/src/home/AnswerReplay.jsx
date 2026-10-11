import { useState } from "react";
import { shortCommit } from "../answers/citations.js";
import HighlightedCode from "../components/HighlightedCode.jsx";
import { EXAMPLE_ANSWER } from "./exampleAnswer.js";

export default function AnswerReplay() {
  const [selected, setSelected] = useState(0);
  const sentence = EXAMPLE_ANSWER.sentences[selected];

  return (
    <section className="replay" aria-label="A saved answer, replayed">
      <div className="replay-bar">
        <span>
          {EXAMPLE_ANSWER.repository} @ {shortCommit(EXAMPLE_ANSWER.commitId)} ·{" "}
          {EXAMPLE_ANSWER.license}
        </span>
        <span>A saved answer from the evaluation · choose a sentence</span>
      </div>
      <div className="replay-answer">
        <p className="replay-question">{EXAMPLE_ANSWER.question}</p>
        {EXAMPLE_ANSWER.sentences.map((item, index) => (
          <button
            type="button"
            className="replay-sentence"
            key={index}
            aria-pressed={index === selected}
            onClick={() => setSelected(index)}
          >
            {item.text}
            <span className="marker">
              1 · {item.startLine}–{item.endLine}
            </span>
          </button>
        ))}
      </div>
      <div className="replay-code">
        <p className="code-caption">
          {EXAMPLE_ANSWER.filePath} · {EXAMPLE_ANSWER.qualifiedName}
        </p>
        <HighlightedCode
          lines={EXAMPLE_ANSWER.lines}
          citedStart={sentence.startLine}
          citedEnd={sentence.endLine}
        />
      </div>
      <p className="replay-bar replay-footer">
        <span>
          Flask {EXAMPLE_ANSWER.version}, used under {EXAMPLE_ANSWER.license}.{" "}
          <a href={EXAMPLE_ANSWER.licenseLink} target="_blank" rel="noreferrer">
            Read the license ({EXAMPLE_ANSWER.licensePath})
          </a>
        </span>
        <a href={EXAMPLE_ANSWER.codeLink} target="_blank" rel="noreferrer">
          Open these lines on GitHub
        </a>
      </p>
    </section>
  );
}
