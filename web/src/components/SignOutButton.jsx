import { useState } from "react";
import { useNavigate } from "react-router";
import { useSession } from "../session/SessionProvider.jsx";
import { AccountNoticeKind } from "./AccountNotice.jsx";

export default function SignOutButton() {
  const { signOut } = useSession();
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);
  const [errorMessage, setErrorMessage] = useState(null);

  async function handleClick() {
    setSigningOut(true);
    setErrorMessage(null);
    try {
      await signOut();
      navigate("/", { replace: true, state: { notice: AccountNoticeKind.SIGNED_OUT } });
    } catch (error) {
      setErrorMessage(error.message);
      setSigningOut(false);
    }
  }

  return (
    <>
      <button type="button" className="button" onClick={handleClick} disabled={signingOut}>
        {signingOut ? "Signing out…" : "Sign out on every device"}
      </button>
      {errorMessage !== null && <p role="alert">Could not sign out: {errorMessage}</p>}
    </>
  );
}
