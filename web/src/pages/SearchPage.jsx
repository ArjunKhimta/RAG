import { useState } from "react";
import { Link, useParams } from "react-router";
import { isSignedOutError } from "../api/account.js";
import { searchRepository } from "../api/repositories.js";
import { lineRangeText } from "../answers/citations.js";
import { MAX_QUESTION_CHARACTERS } from "../components/AskForm.jsx";
import CodeSnippet from "../components/CodeSnippet.jsx";
import LicenseNotice from "../components/LicenseNotice.jsx";
import ReadyRepository from "../components/ReadyRepository.jsx";
import RequireSignIn from "../components/RequireSignIn.jsx";
import { useSession } from "../session/SessionProvider.jsx";
import { messageWithWait } from "../time.js";

export const RESULT_LIMIT_CHOICES = Object.freeze([5, 10, 20]);

const DEFAULT_RESULT_LIMIT = 10;

export default function SearchPage() {
  const { linkId } = useParams();

  return (
    <RequireSignIn title="Search the code" prompt="Sign in to search a repository.">
      <ReadyRepository key={linkId} linkId={linkId}>
        {(link) => <RepositorySearch link={link} />}
      </ReadyRepository>
    </RequireSignIn>
  );
}

function RepositorySearch({ link }) {
  const { markSignedOut } = useSession();
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(DEFAULT_RESULT_LIMIT);
  const [excludeTests, setExcludeTests] = useState(false);
  const [searching, setSearching] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);
  const [reply, setReply] = useState(null);

  const trimmed = query.trim();
  const characterCount = Array.from(trimmed).length;
  const sendable = characterCount > 0 && characterCount <= MAX_QUESTION_CHARACTERS;

  async function handleSubmit(event) {
    event.preventDefault();
    if (!sendable || searching) {
      return;
    }
    setSearching(true);
    setErrorMessage(null);
    try {
      setReply(await searchRepository(link.id, { query: trimmed, limit, excludeTests }));
    } catch (error) {
      if (isSignedOutError(error)) {
        markSignedOut();
        return;
      }
      setErrorMessage(messageWithWait(error));
    } finally {
      setSearching(false);
    }
  }

  return (
    <section>
      <h1>Search {link.repository}</h1>
      <p className="muted">
        Version {link.version}. Search finds the functions and classes closest in meaning to what
        you type, without writing an answer.{" "}
        <Link to={`/repositories/${link.id}/ask`}>Ask a question instead</Link>
      </p>
      <form className="search-form" onSubmit={handleSubmit}>
        <label htmlFor="search-query">Search for</label>
        <input
          id="search-query"
          type="search"
          placeholder="sign the session cookie"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="ask-controls">
          <label className="checkbox">
            <input
              type="checkbox"
              checked={excludeTests}
              onChange={(event) => setExcludeTests(event.target.checked)}
            />
            Leave out test files
          </label>
          <label className="checkbox">
            Show
            <select value={limit} onChange={(event) => setLimit(Number(event.target.value))}>
              {RESULT_LIMIT_CHOICES.map((choice) => (
                <option key={choice} value={choice}>
                  {choice}
                </option>
              ))}
            </select>
            results
          </label>
          <button type="submit" className="button" disabled={!sendable || searching}>
            {searching ? "Searching…" : "Search"}
          </button>
        </div>
        {errorMessage !== null && <p role="alert">Could not search: {errorMessage}</p>}
      </form>
      {reply !== null && <SearchResults reply={reply} />}
    </section>
  );
}

function SearchResults({ reply }) {
  return (
    <div className="search-results">
      {reply.results.length === 0 ? (
        <p className="muted">No code matched.</p>
      ) : (
        reply.results.map((result) => (
          <CodeSnippet
            key={result.rank}
            snippet={result.snippet}
            link={result.link}
            heading={resultHeading(result)}
          />
        ))
      )}
      <LicenseNotice repository={reply.repository} />
    </div>
  );
}

function resultHeading(result) {
  const range = lineRangeText(result.start_line, result.end_line);
  const testNote = result.is_test_file ? " · test file" : "";
  return `${result.rank}. ${result.qualified_name} (${result.kind}) · ${result.file_path} · ${range}${testNote}`;
}
