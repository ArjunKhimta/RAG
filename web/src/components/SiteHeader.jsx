import { Link } from "react-router";
import { SessionStatus, useSession } from "../session/SessionProvider.jsx";
import SignInButton from "./SignInButton.jsx";

export default function SiteHeader() {
  const session = useSession();

  return (
    <header className="site-header">
      <Link to="/" className="site-name">
        RepositoryCite
      </Link>
      <nav className="site-account">
        {session.status === SessionStatus.SIGNED_IN && <AccountLink user={session.user} />}
        {session.status === SessionStatus.SIGNED_OUT && <SignInButton />}
      </nav>
    </header>
  );
}

function AccountLink({ user }) {
  return (
    <Link to="/account" className="account-link">
      <img className="avatar" src={user.avatar_url} alt="" width="28" height="28" />
      <span>{user.login}</span>
    </Link>
  );
}
