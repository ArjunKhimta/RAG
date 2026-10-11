import { Link } from "react-router";
import AccountNotice from "../components/AccountNotice.jsx";
import ServerUnavailable from "../components/ServerUnavailable.jsx";
import SignInButton from "../components/SignInButton.jsx";
import SignInNotice from "../components/SignInNotice.jsx";
import { SessionStatus, useSession } from "../session/SessionProvider.jsx";

export default function HomePage() {
  const session = useSession();

  return (
    <section>
      <SignInNotice />
      <AccountNotice />
      <h1>Ask questions about a Python repository</h1>
      <p>
        Answers cite the exact files and line numbers they come from, with a link to the code on
        GitHub.
      </p>
      {session.status === SessionStatus.SIGNED_OUT && (
        <p>
          <SignInButton />
        </p>
      )}
      {session.status === SessionStatus.SIGNED_IN && (
        <p>
          Signed in as {session.user.name ?? session.user.login}.{" "}
          <Link to="/account">Your account</Link>
        </p>
      )}
      {session.status === SessionStatus.UNAVAILABLE && <ServerUnavailable />}
    </section>
  );
}
