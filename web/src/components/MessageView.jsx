import { useEffect } from "react";
import { Timing, finishTiming } from "../timing.js";
import AnswerView from "./AnswerView.jsx";

export default function MessageView({ message }) {
  useEffect(() => {
    finishTiming(Timing.SHOWN_ANSWER);
  }, []);

  return (
    <article className="message">
      <p className="message-question">
        {message.question}
        {message.exclude_tests && <span className="muted"> · test files left out</span>}
      </p>
      <AnswerView messageId={message.id} reply={message.reply} />
    </article>
  );
}
