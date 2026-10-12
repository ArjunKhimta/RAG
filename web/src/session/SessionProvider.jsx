import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { deleteAccount, fetchCurrentUser, signOutEverywhere } from "../api/account.js";
import { ApiRequestError } from "../api/client.js";
import { wakeServers } from "../api/wakeServer.js";

export const SessionStatus = Object.freeze({
  LOADING: "loading",
  SIGNED_IN: "signed_in",
  SIGNED_OUT: "signed_out",
  WAKING: "waking",
  UNAVAILABLE: "unavailable",
});

export const WAKE_RETRY_MILLISECONDS = 3_000;

export const WAKE_WAIT_MILLISECONDS = 90_000;

const WAKING_SERVER_STATUSES = new Set([0, 502, 503, 504]);

const LOADING_SESSION = { status: SessionStatus.LOADING, user: null };

const SIGNED_OUT_SESSION = { status: SessionStatus.SIGNED_OUT, user: null };

const WAKING_SESSION = { status: SessionStatus.WAKING, user: null };

const UNAVAILABLE_SESSION = { status: SessionStatus.UNAVAILABLE, user: null };

const SessionContext = createContext(null);

async function askForSession() {
  try {
    const user = await fetchCurrentUser();
    return user === null ? SIGNED_OUT_SESSION : { status: SessionStatus.SIGNED_IN, user };
  } catch (error) {
    return isServerWaking(error) ? WAKING_SESSION : UNAVAILABLE_SESSION;
  }
}

function isServerWaking(error) {
  if (!(error instanceof ApiRequestError)) {
    return false;
  }
  return !error.isApiReply || WAKING_SERVER_STATUSES.has(error.status);
}

function startLoadingSession(onSession) {
  const startedAt = Date.now();
  let active = true;
  let retryTimer = null;

  async function attempt() {
    const session = await askForSession();
    if (!active) {
      return;
    }
    if (session.status !== SessionStatus.WAKING) {
      onSession(session);
      return;
    }
    const waitedMilliseconds = Date.now() - startedAt;
    if (waitedMilliseconds + WAKE_RETRY_MILLISECONDS > WAKE_WAIT_MILLISECONDS) {
      onSession(UNAVAILABLE_SESSION);
      return;
    }
    onSession(WAKING_SESSION);
    retryTimer = setTimeout(attempt, WAKE_RETRY_MILLISECONDS);
  }

  wakeServers();
  attempt();
  return () => {
    active = false;
    clearTimeout(retryTimer);
  };
}

export function SessionProvider({ children }) {
  const [session, setSession] = useState(LOADING_SESSION);
  const [loadCount, setLoadCount] = useState(0);

  useEffect(() => startLoadingSession(setSession), [loadCount]);

  const refresh = useCallback(() => {
    setSession(LOADING_SESSION);
    setLoadCount((count) => count + 1);
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
