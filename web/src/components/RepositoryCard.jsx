import { useEffect, useState } from "react";
import { Link } from "react-router";
import { isSignedOutError } from "../api/account.js";
import { fetchRepository, removeRepository, resumeRepository } from "../api/repositories.js";
import { useCurrentTime } from "../hooks/useCurrentTime.js";
import { gitHubCommitLink, gitHubRepositoryLink, safeGitHubLink } from "../links.js";
import {
  LinkStatus,
  resumeAvailability,
  stageDescription,
  statusLabel,
} from "../repositories/linkStatus.js";
import { useSession } from "../session/SessionProvider.jsx";
import { formatLocalTime, messageWithWait } from "../time.js";

export const POLL_INTERVAL_MILLISECONDS = 3000;

export const CLOCK_REFRESH_MILLISECONDS = 30000;

const NOT_FOUND_STATUS = 404;

const SHORT_COMMIT_LENGTH = 7;

export default function RepositoryCard({ link, onChange, onRemoved }) {
  const { markSignedOut } = useSession();
  const [pollAttempt, setPollAttempt] = useState(0);
  const [pollErrorMessage, setPollErrorMessage] = useState(null);
  const [actionErrorMessage, setActionErrorMessage] = useState(null);
  const [busy, setBusy] = useState(false);
  const [confirmingRemoval, setConfirmingRemoval] = useState(false);
  const now = useCurrentTime(
    CLOCK_REFRESH_MILLISECONDS,
    link.status === LinkStatus.WAITING_FOR_QUOTA,
  );

  useEffect(() => {
    if (link.status !== LinkStatus.INDEXING) {
      return undefined;
    }
    let active = true;
    const timer = setTimeout(async () => {
      try {
        const updated = await fetchRepository(link.id);
        if (active) {
          setPollErrorMessage(null);
          onChange(updated);
        }
      } catch (error) {
        if (!active) {
          return;
        }
        if (isSignedOutError(error)) {
          markSignedOut();
        } else if (error.status === NOT_FOUND_STATUS) {
          onRemoved(link.id);
        } else {
          setPollErrorMessage(error.message);
          setPollAttempt((attempt) => attempt + 1);
        }
      }
    }, POLL_INTERVAL_MILLISECONDS);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [link, pollAttempt, onChange, onRemoved, markSignedOut]);

  async function runAction(action) {
    setBusy(true);
    setActionErrorMessage(null);
    try {
      await action();
    } catch (error) {
      if (isSignedOutError(error)) {
        markSignedOut();
        return;
      }
      setActionErrorMessage(messageWithWait(error));
    } finally {
      setBusy(false);
    }
  }

  function handleResume() {
    return runAction(async () => {
      onChange(await resumeRepository(link.id));
    });
  }

  function handleRemove() {
    return runAction(async () => {
      await removeRepository(link.id);
      onRemoved(link.id);
    });
  }

  const resume = resumeAvailability(link, now);

  return (
    <li className="repository-card">
      <div className="repository-heading">
        <a href={gitHubRepositoryLink(link.repository)} target="_blank" rel="noreferrer">
          {link.repository}
        </a>
        <span className="repository-version">{link.version}</span>
        <span className={`status status-${link.status}`}>{statusLabel(link.status)}</span>
      </div>

      {link.status === LinkStatus.INDEXING && <IndexingProgress link={link} />}
      {link.status === LinkStatus.READY && <IndexedDetails link={link} />}
      {link.status === LinkStatus.WAITING_FOR_QUOTA && (
        <WaitingDetails link={link} resumeAfter={resume.resumeAfter} />
      )}
      {link.status === LinkStatus.INTERRUPTED && (
        <p>Indexing stopped before it finished. Resuming reuses the work already done.</p>
      )}
      {link.status === LinkStatus.FAILED && (
        <FailedDetails link={link} canResume={resume.canResume} />
      )}

      {pollErrorMessage !== null && (
        <p role="alert">Could not refresh the progress: {pollErrorMessage}. Trying again…</p>
      )}
      {actionErrorMessage !== null && <p role="alert">{actionErrorMessage}</p>}

      <div className="repository-actions">
        {link.status === LinkStatus.READY && (
          <>
            <Link to={`/repositories/${link.id}/ask`} className="button button-outline">
              Ask a question
            </Link>
            <Link to={`/repositories/${link.id}/search`} className="button">
              Search the code
            </Link>
            <Link to={`/repositories/${link.id}/overview`} className="button">
              Overview
            </Link>
          </>
        )}
        {resume.canResume && (
          <button type="button" className="button" onClick={handleResume} disabled={busy}>
            Resume indexing
          </button>
        )}
        {confirmingRemoval ? (
          <RemovalConfirmation
            link={link}
            busy={busy}
            onConfirm={handleRemove}
            onCancel={() => setConfirmingRemoval(false)}
          />
        ) : (
          <button
            type="button"
            className="button-plain"
            onClick={() => setConfirmingRemoval(true)}
            disabled={busy}
          >
            Remove
          </button>
        )}
      </div>
    </li>
  );
}

function IndexingProgress({ link }) {
  const progress = link.progress;
  const total = progress === null ? null : progress.to_embed;
  const showCount = link.stage === "embedding" && total !== null && total > 0;

  return (
    <div className="indexing-progress">
      <p>{stageDescription(link.stage)}…</p>
      {showCount ? (
        <>
          <progress value={progress.embedded} max={total} />
          <p className="muted">
            {progress.embedded} of {total} pieces of code embedded
          </p>
        </>
      ) : (
        <progress />
      )}
    </div>
  );
}

function IndexedDetails({ link }) {
  if (link.indexed === null) {
    return null;
  }
  const { commit_id: commitId, license } = link.indexed;
  const licenseLink = safeGitHubLink(license?.link);
  const licenseName = license?.spdx_id ?? license?.name ?? "Unknown license";

  return (
    <p className="muted">
      Indexed at commit{" "}
      <a href={gitHubCommitLink(link.repository, commitId)} target="_blank" rel="noreferrer">
        <code>{commitId.slice(0, SHORT_COMMIT_LENGTH)}</code>
      </a>
      {" · License: "}
      {licenseLink === null ? (
        licenseName
      ) : (
        <a href={licenseLink} target="_blank" rel="noreferrer">
          {licenseName}
        </a>
      )}
    </p>
  );
}

function WaitingDetails({ link, resumeAfter }) {
  const embedded = link.progress?.embedded;
  const total = link.progress?.to_embed;
  const hasCount = typeof embedded === "number" && typeof total === "number";

  return (
    <p>
      Today&apos;s embedding quota is used up
      {hasCount && `, after ${embedded} of ${total} pieces of code`}.{" "}
      {resumeAfter === null
        ? "You can resume now."
        : `You can resume after ${formatLocalTime(resumeAfter)}; the work done so far is kept.`}
    </p>
  );
}

function FailedDetails({ link, canResume }) {
  const message = link.error?.message ?? "Indexing failed.";

  return (
    <p>
      {message}
      {canResume ? " This may work if you try again." : " Trying again would fail the same way."}
    </p>
  );
}

function RemovalConfirmation({ link, busy, onConfirm, onCancel }) {
  return (
    <div className="removal-confirmation">
      <span>
        Remove {link.repository} {link.version} from your list? Its index stays for others.
      </span>
      <button type="button" className="button button-danger" onClick={onConfirm} disabled={busy}>
        Remove
      </button>
      <button type="button" className="button-plain" onClick={onCancel} disabled={busy}>
        Cancel
      </button>
    </div>
  );
}
