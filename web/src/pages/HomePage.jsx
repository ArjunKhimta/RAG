import { Link } from "react-router";
import AccountNotice from "../components/AccountNotice.jsx";
import ServerUnavailable from "../components/ServerUnavailable.jsx";
import ServerWaking from "../components/ServerWaking.jsx";
import SignInButton from "../components/SignInButton.jsx";
import SignInNotice from "../components/SignInNotice.jsx";
import AnswerPipeline from "../home/AnswerPipeline.jsx";
import AnswerReplay from "../home/AnswerReplay.jsx";
import MeasuredResults from "../home/MeasuredResults.jsx";
import { SessionStatus, useSession } from "../session/SessionProvider.jsx";

export default function HomePage() {
  const session = useSession();

  return (
    <>
      <SignInNotice />
      <AccountNotice />
      <section className="hero">
        <h1>Ask questions about a Python repository</h1>
        <p className="hero-lead">
          Answers cite the exact files and line numbers they come from, with a link to the code on
          GitHub. An answer whose citations point outside the code it was given is never shown.
        </p>
        {session.status === SessionStatus.SIGNED_OUT && (
          <div className="hero-actions">
            <SignInButton prominent />
            <span className="muted">
              Before signing in, see <Link to="/privacy">what is stored and who receives it</Link>.
            </span>
          </div>
        )}
        {session.status === SessionStatus.SIGNED_IN && (
          <p className="hero-actions">
            <span>Signed in as {session.user.name ?? session.user.login}.</span>
            <Link to="/repositories" className="button button-primary">
              Go to your repositories
            </Link>
          </p>
        )}
        {session.status === SessionStatus.WAKING && <ServerWaking />}
        {session.status === SessionStatus.UNAVAILABLE && <ServerUnavailable />}
      </section>

      <AnswerReplay />

      <section className="rail">
        <h2 className="rail-label">How an answer is made</h2>
        <div className="rail-body">
          <AnswerPipeline />
        </div>
      </section>

      <section className="rail">
        <h2 className="rail-label">Measured, not claimed</h2>
        <div className="rail-body">
          <MeasuredResults />
        </div>
      </section>

      <section className="rail">
        <h2 className="rail-label">Start</h2>
        <div className="rail-body">
          <ol className="start-steps">
            <li>Sign in with GitHub. No permissions are requested, only your public profile.</li>
            <li>
              Add a public Python repository with an open-source license, at a tag, branch, or
              commit.
            </li>
            <li>Ask in plain English.</li>
          </ol>
        </div>
      </section>
    </>
  );
}
