import { useEffect, useState } from "react";
import { fetchUsage, isSignedOutError } from "../api/account.js";
import { useSession } from "../session/SessionProvider.jsx";
import { formatLocalTime } from "../time.js";

const USAGE_ROWS = Object.freeze([
  { key: "questions", label: "Questions today" },
  { key: "searches", label: "Searches today" },
  { key: "indexing_requests", label: "New repositories to index today" },
  { key: "linked_repositories", label: "Linked repositories" },
]);

export default function UsagePanel() {
  const { markSignedOut } = useSession();
  const [usage, setUsage] = useState(null);
  const [errorMessage, setErrorMessage] = useState(null);

  useEffect(() => {
    let active = true;
    fetchUsage().then(
      (loaded) => {
        if (active) {
          setUsage(loaded);
        }
      },
      (error) => {
        if (!active) {
          return;
        }
        if (isSignedOutError(error)) {
          markSignedOut();
          return;
        }
        setErrorMessage(error.message);
      },
    );
    return () => {
      active = false;
    };
  }, [markSignedOut]);

  if (errorMessage !== null) {
    return <p role="alert">Could not load your usage: {errorMessage}</p>;
  }
  if (usage === null) {
    return <p>Loading your usage…</p>;
  }

  return (
    <>
      <table className="usage-table">
        <tbody>
          {USAGE_ROWS.map((row) => (
            <tr key={row.key}>
              <th scope="row">{row.label}</th>
              <td>
                {usage[row.key].used} of {usage[row.key].limit}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">
        Daily counts reset at midnight Pacific time, which is {formatLocalTime(usage.resets_at)}{" "}
        where you are.
      </p>
    </>
  );
}
