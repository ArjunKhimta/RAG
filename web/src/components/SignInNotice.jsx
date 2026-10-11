import { useSearchParams } from "react-router";

const SIGN_IN_OUTCOME_PARAMETER = "sign_in";

const SIGN_IN_MESSAGES = Object.freeze({
  denied: "Sign-in was cancelled on GitHub, so nothing was saved.",
  expired:
    "That sign-in took longer than 10 minutes or started in another browser. Please try again.",
  failed: "Sign-in did not work. Please try again in a moment.",
  rate_limited:
    "Too many sign-in attempts came from your network. Please wait up to 15 minutes and try again.",
});

export function signInMessageFor(outcome) {
  return Object.hasOwn(SIGN_IN_MESSAGES, outcome) ? SIGN_IN_MESSAGES[outcome] : null;
}

export default function SignInNotice() {
  const [searchParams, setSearchParams] = useSearchParams();
  const message = signInMessageFor(searchParams.get(SIGN_IN_OUTCOME_PARAMETER));

  if (message === null) {
    return null;
  }

  function dismiss() {
    const remaining = new URLSearchParams(searchParams);
    remaining.delete(SIGN_IN_OUTCOME_PARAMETER);
    setSearchParams(remaining, { replace: true });
  }

  return (
    <div className="notice notice-warning" role="alert">
      <p>{message}</p>
      <button type="button" className="button-plain" onClick={dismiss}>
        Dismiss
      </button>
    </div>
  );
}
