import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { deleteAccount, fetchCurrentUser, signOutEverywhere } from "../api/account.js";

export const SessionStatus = Object.freeze({
  LOADING: "loading",
  SIGNED_IN: "signed_in",
  SIGNED_OUT: "signed_out",
  UNAVAILABLE: "unavailable",
});

const LOADING_SESSION = { status: SessionStatus.LOADING, user: null };

const SIGNED_OUT_SESSION = { status: SessionStatus.SIGNED_OUT, user: null };

const UNAVAILABLE_SESSION = { status: SessionStatus.UNAVAILABLE, user: null };

const SessionContext = createContext(null);

async function loadSession() {
  try {
    const user = await fetchCurrentUser();
    return user === null ? SIGNED_OUT_SESSION : { status: SessionStatus.SIGNED_IN, user };
  } catch {
    return UNAVAILABLE_SESSION;
  }
}

export function SessionProvider({ children }) {
  const [session, setSession] = useState(LOADING_SESSION);

  useEffect(() => {
    let active = true;
    loadSession().then((loaded) => {
      if (active) {
        setSession(loaded);
      }
    });
    return () => {
      active = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    setSession(await loadSession());
  }, []);

  const markSignedOut = useCallback(() => {
    setSession(SIGNED_OUT_SESSION);
  }, []);

  const signOut = useCallback(async () => {
    await signOutEverywhere();
    setSession(SIGNED_OUT_SESSION);
  }, []);

  const removeAccount = useCallback(async () => {
    await deleteAccount();
    setSession(SIGNED_OUT_SESSION);
  }, []);

  const value = useMemo(
    () => ({ ...session, refresh, markSignedOut, signOut, removeAccount }),
    [session, refresh, markSignedOut, signOut, removeAccount],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  const session = useContext(SessionContext);
  if (session === null) {
    throw new Error("useSession must be used inside a SessionProvider");
  }
  return session;
}
