import { useEffect } from "react";
import { Timing, finishTiming } from "../timing.js";
import AnswerView from "./AnswerView.jsx";
import OlderCommitNote from "./OlderCommitNote.jsx";

export default function MessageView({ message, currentCommit = null }) {
  useEffect(() => {
    finishTiming(Timing.SHOWN_ANSWER);
  }, []);

  return (
    <article className="message">
      <p className="message-question">
        {message.question}
        {message.exclude_tests && <span className="muted"> · test files left out</span>}
      </p>
      <OlderCommitNote
        answerCommit={message.reply.repository.commit_id}
        currentCommit={currentCommit}
      />
      <AnswerView messageId={message.id} reply={message.reply} />
    </article>
  );
}
