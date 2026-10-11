import { shortCommit } from "../answers/citations.js";
import { gitHubCommitLink, safeGitHubLink } from "../links.js";

export default function LicenseNotice({ repository }) {
  const license = repository.license ?? {};
  const licenseName = license.spdx_id ?? license.name ?? "its license";
  const licenseLink = safeGitHubLink(license.link);

  return (
    <p className="license-notice muted">
      Code from {repository.name} at commit{" "}
      <a href={gitHubCommitLink(repository.name, repository.commit_id)} target="_blank" rel="noreferrer">
        <code>{shortCommit(repository.commit_id)}</code>
      </a>
      , used under {licenseName}.{" "}
      {licenseLink === null ? (
        "License file link unavailable."
      ) : (
        <a href={licenseLink} target="_blank" rel="noreferrer">
          Read the license ({license.path})
        </a>
      )}
    </p>
  );
}
