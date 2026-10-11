import { shortCommit } from "../answers/citations.js";

export default function OlderCommitNote({ answerCommit, currentCommit }) {
  if (typeof currentCommit !== "string" || answerCommit === currentCommit) {
    return null;
  }
  return (
    <p className="older-commit-note">
      Answered from commit <code>{shortCommit(answerCommit)}</code>; the repository has since been
      indexed again at <code>{shortCommit(currentCommit)}</code>, so the code may have changed. The
      snippets and links below still show the code as it was. Ask again for an answer from the
      current code.
    </p>
  );
}
