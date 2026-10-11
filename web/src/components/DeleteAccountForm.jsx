import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { GITHUB_AUTHORIZED_APPS_URL } from "../privacy/policy.js";
import { useSession } from "../session/SessionProvider.jsx";
import { AccountNoticeKind } from "./AccountNotice.jsx";

export function confirmationMatches(typed, login) {
  return typed.trim().toLowerCase() === login.toLowerCase();
}

export default function DeleteAccountForm() {
  const { user, removeAccount } = useSession();
  const navigate = useNavigate();
  const [typedLogin, setTypedLogin] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);

  const confirmed = confirmationMatches(typedLogin, user.login);

  async function handleSubmit(event) {
    event.preventDefault();
    if (!confirmed || deleting) {
      return;
    }
    setDeleting(true);
    setErrorMessage(null);
    try {
      await removeAccount();
      navigate("/", { replace: true, state: { notice: AccountNoticeKind.ACCOUNT_DELETED } });
    } catch (error) {
      setErrorMessage(error.message);
      setDeleting(false);
    }
  }

  return (
    <form className="danger-zone" onSubmit={handleSubmit}>
      <p>This permanently deletes:</p>
      <ul>
        <li>your account (GitHub ID, username, name, and avatar link)</li>
        <li>your list of linked repositories</li>
        <li>your conversations and every question and answer in them</li>
        <li>your daily usage counts</li>
      </ul>
      <p>
        Repositories you indexed stay searchable for others, because the search index holds code,
        not anything about you. To also remove this site from your GitHub account, revoke it under{" "}
        <a href={GITHUB_AUTHORIZED_APPS_URL} target="_blank" rel="noreferrer">
          GitHub&apos;s authorized apps
        </a>
        . The <Link to="/privacy">privacy page</Link> explains what is stored and for how long.
      </p>
      <label htmlFor="confirm-login">
        Type your GitHub username, <strong>{user.login}</strong>, to confirm
      </label>
      <input
        id="confirm-login"
        type="text"
        autoComplete="off"
        spellCheck="false"
        value={typedLogin}
        onChange={(event) => setTypedLogin(event.target.value)}
      />
      {errorMessage !== null && <p role="alert">Could not delete your account: {errorMessage}</p>}
      <button type="submit" className="button button-danger" disabled={!confirmed || deleting}>
        {deleting ? "Deleting…" : "Delete my account"}
      </button>
    </form>
  );
}
