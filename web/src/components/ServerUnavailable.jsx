import { useSession } from "../session/SessionProvider.jsx";

export default function ServerUnavailable() {
  const session = useSession();

  return (
    <div role="alert">
      <p>The server cannot be reached right now, so we cannot tell whether you are signed in.</p>
      <p>
        <button type="button" className="button button-outline" onClick={session.refresh}>
          Try again
        </button>
      </p>
    </div>
  );
}
