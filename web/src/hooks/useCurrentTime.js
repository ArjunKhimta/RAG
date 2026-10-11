import { useEffect, useState } from "react";

export function useCurrentTime(refreshMilliseconds, enabled) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!enabled) {
      return undefined;
    }
    const timer = setInterval(() => setNow(Date.now()), refreshMilliseconds);
    return () => clearInterval(timer);
  }, [refreshMilliseconds, enabled]);

  return now;
}
