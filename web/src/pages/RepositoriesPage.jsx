import { useCallback, useEffect, useState } from "react";
import { isSignedOutError } from "../api/account.js";
import { listRepositories } from "../api/repositories.js";
import AddRepositoryForm from "../components/AddRepositoryForm.jsx";
import RepositoryCard from "../components/RepositoryCard.jsx";
import ServerUnavailable from "../components/ServerUnavailable.jsx";
import SignInButton from "../components/SignInButton.jsx";
import { SessionStatus, useSession } from "../session/SessionProvider.jsx";

export default function RepositoriesPage() {
  const session = useSession();

  if (session.status === SessionStatus.LOADING) {
    return <p>Loading…</p>;
  }
  if (session.status === SessionStatus.UNAVAILABLE) {
    return <ServerUnavailable />;
  }
  if (session.status === SessionStatus.SIGNED_OUT) {
    return (
      <section>
        <h1>Your repositories</h1>
        <p>Sign in to add repositories and ask questions about them.</p>
        <p>
          <SignInButton />
        </p>
      </section>
    );
  }
  return <RepositoryList />;
}

function RepositoryList() {
  const { markSignedOut } = useSession();
  const [links, setLinks] = useState(null);
  const [errorMessage, setErrorMessage] = useState(null);

  useEffect(() => {
    let active = true;
    listRepositories().then(
      (loaded) => {
        if (active) {
          setLinks(loaded);
        }
      },
      (error) => {
        if (!active) {
          return;
        }
        if (isSignedOutError(error)) {
          markSignedOut();
          return;
        }
        setErrorMessage(error.message);
      },
    );
    return () => {
      active = false;
    };
  }, [markSignedOut]);

  const replaceLink = useCallback((updated) => {
    setLinks((current) => current.map((link) => (link.id === updated.id ? updated : link)));
  }, []);

  const dropLink = useCallback((linkId) => {
    setLinks((current) => current.filter((link) => link.id !== linkId));
  }, []);

  const addLink = useCallback((added) => {
    setLinks((current) => [added, ...current.filter((link) => link.id !== added.id)]);
  }, []);

  return (
    <section>
      <h1>Your repositories</h1>
      <AddRepositoryForm onAdded={addLink} />
      {errorMessage !== null && (
        <p role="alert">Could not load your repositories: {errorMessage}</p>
      )}
      {errorMessage === null && links === null && <p>Loading your repositories…</p>}
      {links !== null && links.length === 0 && (
        <p className="muted">You have not added any repositories yet.</p>
      )}
      {links !== null && links.length > 0 && (
        <ul className="repository-list">
          {links.map((link) => (
            <RepositoryCard key={link.id} link={link} onChange={replaceLink} onRemoved={dropLink} />
          ))}
        </ul>
      )}
    </section>
  );
}
