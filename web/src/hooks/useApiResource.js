import { useEffect, useState } from "react";
import { isSignedOutError } from "../api/account.js";
import { useSession } from "../session/SessionProvider.jsx";

export function useApiResource(load) {
  const { markSignedOut } = useSession();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let active = true;
    load().then(
      (loaded) => {
        if (active) {
          setData(loaded);
        }
      },
      (failure) => {
        if (!active) {
          return;
        }
        if (isSignedOutError(failure)) {
          markSignedOut();
          return;
        }
        setError(failure);
      },
    );
    return () => {
      active = false;
    };
  }, [load, markSignedOut]);

  return { data, error, setData };
}
