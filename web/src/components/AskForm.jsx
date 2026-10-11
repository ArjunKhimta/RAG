import { useState } from "react";
import { isSignedOutError } from "../api/account.js";
import { Link } from "react-router";
import { useSession } from "../session/SessionProvider.jsx";
import { messageWithWait } from "../time.js";

export const MAX_QUESTION_CHARACTERS = 1000;

export default function AskForm({ onAsk }) {
  const { markSignedOut } = useSession();
  const [question, setQuestion] = useState("");
  const [excludeTests, setExcludeTests] = useState(false);
  const [asking, setAsking] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);

  const trimmed = question.trim();
  const characterCount = Array.from(trimmed).length;
  const sendable = characterCount > 0 && characterCount <= MAX_QUESTION_CHARACTERS;

  async function handleSubmit(event) {
    event.preventDefault();
    if (!sendable || asking) {
      return;
    }
    setAsking(true);
    setErrorMessage(null);
    try {
      await onAsk(trimmed, excludeTests);
      setQuestion("");
    } catch (error) {
      if (isSignedOutError(error)) {
        markSignedOut();
        return;
      }
      setErrorMessage(messageWithWait(error));
    } finally {
      setAsking(false);
    }
  }

  return (
    <form className="ask-form" onSubmit={handleSubmit}>
      <label htmlFor="question">Your question</label>
      <textarea
        id="question"
        rows="3"
        placeholder="How does Flask sign the session cookie?"
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
        disabled={asking}
      />
      <div className="ask-controls">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={excludeTests}
            onChange={(event) => setExcludeTests(event.target.checked)}
            disabled={asking}
          />
          Leave out test files
        </label>
        <span className={characterCount > MAX_QUESTION_CHARACTERS ? "over-limit" : "muted"}>
          {characterCount} / {MAX_QUESTION_CHARACTERS}
        </span>
        <button type="submit" className="button" disabled={!sendable || asking}>
          {asking ? "Answering…" : "Ask"}
        </button>
      </div>
      <p className="muted">
        Questions are sent to Google&apos;s Gemini on its free tier, where Google may read them;
        leave out anything personal or confidential. <Link to="/privacy">Privacy</Link>
      </p>
      {asking && (
        <p className="muted" role="status">
          Searching the code and writing an answer; this usually takes a few seconds.
        </p>
      )}
      {errorMessage !== null && <p role="alert">Could not answer: {errorMessage}</p>}
    </form>
  );
}
