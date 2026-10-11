import { SessionStatus, useSession } from "../session/SessionProvider.jsx";
import ServerUnavailable from "./ServerUnavailable.jsx";
import SignInButton from "./SignInButton.jsx";

export default function RequireSignIn({ title, prompt, children }) {
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
        <h1>{title}</h1>
        <p>{prompt}</p>
        <p>
          <SignInButton />
        </p>
      </section>
    );
  }
  return children;
}
