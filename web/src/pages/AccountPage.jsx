import DeleteAccountForm from "../components/DeleteAccountForm.jsx";
import ServerUnavailable from "../components/ServerUnavailable.jsx";
import SignInButton from "../components/SignInButton.jsx";
import SignOutButton from "../components/SignOutButton.jsx";
import UsagePanel from "../components/UsagePanel.jsx";
import { SessionStatus, useSession } from "../session/SessionProvider.jsx";

export default function AccountPage() {
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
        <h1>Your account</h1>
        <p>Sign in to see your account.</p>
        <p>
          <SignInButton />
        </p>
      </section>
    );
  }

  const { user } = session;

  return (
    <section>
      <h1>Your account</h1>
      <div className="profile">
        <img className="avatar-large" src={user.avatar_url} alt="" width="64" height="64" />
        <div>
          {user.name !== null && <p className="profile-name">{user.name}</p>}
          <p className="muted">{user.login} on GitHub</p>
        </div>
      </div>

      <h2>Today&apos;s usage</h2>
      <UsagePanel />

      <h2>Sign out</h2>
      <p>Signing out ends your session on every device and browser at once.</p>
      <SignOutButton />

      <h2>Delete your account</h2>
      <DeleteAccountForm />
    </section>
  );
}
