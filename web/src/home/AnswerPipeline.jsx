import { useState } from "react";
import { ANSWER_STAGES, DEFAULT_STAGE_INDEX, QUESTION_TIMINGS } from "./pipeline.js";

export default function AnswerPipeline() {
  const [selected, setSelected] = useState(DEFAULT_STAGE_INDEX);
  const stage = ANSWER_STAGES[selected];

  return (
    <div>
      <div className="tabs" role="group" aria-label="Stages">
        {ANSWER_STAGES.map((item, index) => (
          <button
            type="button"
            className="tab"
            key={item.name}
            aria-pressed={index === selected}
            onClick={() => setSelected(index)}
          >
            {item.name}
          </button>
        ))}
      </div>
      <div className="stage-detail">
        <div className="stage-text">
          <span className="small-label">{stage.when}</span>
          <p>{stage.detail}</p>
        </div>
        <p className="stage-figure">
          {stage.figure}
          <span>{stage.figureLabel}</span>
        </p>
      </div>
      <QuestionTimeBar />
    </div>
  );
}

function QuestionTimeBar() {
  const stages = QUESTION_TIMINGS.stages;
  const total = stages.reduce((sum, stage) => sum + stage.milliseconds, 0);

  return (
    <figure className="time-figure">
      <figcaption className="small-label">
        Where {formatSeconds(total)} of a question goes: medians of 5 questions, measured in the
        service
      </figcaption>
      <div className="time-bar" aria-hidden="true">
        {stages.map((stage) => (
          <span
            key={stage.name}
            className={stage.className}
            style={{ flexGrow: stage.milliseconds }}
          />
        ))}
      </div>
      <ul className="time-key">
        {stages.map((stage) => (
          <li key={stage.name}>
            <i className={stage.className} aria-hidden="true" />
            {stage.name} {stage.milliseconds.toLocaleString("en-US")} ms
          </li>
        ))}
      </ul>
    </figure>
  );
}

function formatSeconds(milliseconds) {
  return `${(milliseconds / 1000).toFixed(1)} s`;
}
