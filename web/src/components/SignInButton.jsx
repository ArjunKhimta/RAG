import { SIGN_IN_PATH } from "../api/account.js";

export default function SignInButton({ prominent = false }) {
  const variant = prominent ? "button-primary" : "button-outline";

  return (
    <a className={`button ${variant}`} href={SIGN_IN_PATH}>
      Sign in with GitHub
    </a>
  );
}
