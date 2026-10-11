import { useState } from "react";
import { isSignedOutError } from "../api/account.js";
import { addRepository } from "../api/repositories.js";
import { normalizeRepositoryInput } from "../repositories/linkStatus.js";
import { useSession } from "../session/SessionProvider.jsx";
import { messageWithWait } from "../time.js";

export default function AddRepositoryForm({ onAdded }) {
  const { markSignedOut } = useSession();
  const [repository, setRepository] = useState("");
  const [version, setVersion] = useState("");
  const [adding, setAdding] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);

  const normalizedRepository = normalizeRepositoryInput(repository);
  const trimmedVersion = version.trim();
  const complete = normalizedRepository.length > 0 && trimmedVersion.length > 0;

  async function handleSubmit(event) {
    event.preventDefault();
    if (!complete || adding) {
      return;
    }
    setAdding(true);
    setErrorMessage(null);
    try {
      const link = await addRepository(normalizedRepository, trimmedVersion);
      onAdded(link);
      setRepository("");
      setVersion("");
    } catch (error) {
      if (isSignedOutError(error)) {
        markSignedOut();
        return;
      }
      setErrorMessage(messageWithWait(error));
    } finally {
      setAdding(false);
    }
  }

  return (
    <form className="add-repository" onSubmit={handleSubmit}>
      <div className="field">
        <label htmlFor="repository-name">Repository</label>
        <input
          id="repository-name"
          type="text"
          placeholder="pallets/flask"
          autoComplete="off"
          spellCheck="false"
          value={repository}
          onChange={(event) => setRepository(event.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="repository-version">Version</label>
        <input
          id="repository-version"
          type="text"
          placeholder="3.1.3"
          autoComplete="off"
          spellCheck="false"
          value={version}
          onChange={(event) => setVersion(event.target.value)}
        />
      </div>
      <button type="submit" className="button" disabled={!complete || adding}>
        {adding ? "Adding…" : "Add repository"}
      </button>
      <p className="muted field-help">
        A public Python repository on GitHub with an open-source license. The version is a tag
        (like 3.1.3), a branch, or a commit ID.
      </p>
      {errorMessage !== null && (
        <p role="alert" className="field-help">
          Could not add the repository: {errorMessage}
        </p>
      )}
    </form>
  );
}
