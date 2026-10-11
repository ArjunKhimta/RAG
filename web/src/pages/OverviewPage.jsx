import { useCallback, useMemo } from "react";
import { Link, useParams } from "react-router";
import { fetchOverview } from "../api/repositories.js";
import LicenseNotice from "../components/LicenseNotice.jsx";
import OverviewDiagram from "../components/OverviewDiagram.jsx";
import ReadyRepository from "../components/ReadyRepository.jsx";
import RequireSignIn from "../components/RequireSignIn.jsx";
import { useApiResource } from "../hooks/useApiResource.js";
import { safeGitHubLink } from "../links.js";
import { buildDiagram } from "../overview/diagram.js";

export default function OverviewPage() {
  const { linkId } = useParams();

  return (
    <RequireSignIn title="Repository overview" prompt="Sign in to see a repository's overview.">
      <ReadyRepository key={linkId} linkId={linkId}>
        {(link) => <RepositoryOverview link={link} />}
      </ReadyRepository>
    </RequireSignIn>
  );
}

function RepositoryOverview({ link }) {
  const load = useCallback(() => fetchOverview(link.id), [link.id]);
  const { data: overview, error } = useApiResource(load);

  return (
    <section className="overview">
      <h1>Overview of {link.repository}</h1>
      <p className="muted">
        Version {link.version}. <Link to={`/repositories/${link.id}/ask`}>Ask a question</Link>{" "}
        · <Link to={`/repositories/${link.id}/search`}>Search the code</Link>
      </p>
      {error !== null && <p role="alert">Could not load the overview: {error.message}</p>}
      {error === null && overview === null && <p>Loading the overview…</p>}
      {overview !== null && <OverviewDetails overview={overview} />}
    </section>
  );
}

function OverviewDetails({ overview }) {
  const diagram = useMemo(() => buildDiagram(overview), [overview]);
  const { totals } = overview;

  return (
    <>
      <p>
        {totals.files} source files with {totals.classes} classes and {totals.functions} functions.
        The indexer linked {totals.calls} calls between functions: {totals.calls_between_files}{" "}
        between files and {totals.calls_within_files} within a file.
      </p>

      <h2>How the files call each other</h2>
      {diagram.shownLinkCount === 0 ? (
        <p className="muted">No calls between files were found.</p>
      ) : (
        <>
          <p className="muted">
            The {diagram.shownFileCount} most connected of the {diagram.connectedFileCount} files
            that call or are called by another file, grouped by folder. Each number counts the
            pairs of a calling function and a called function.
          </p>
          <OverviewDiagram source={diagram.source} />
        </>
      )}
      <p className="muted">
        Only calls the indexer could link are counted: calls into other libraries, and many calls
        on objects whose type it cannot tell, are left out, as are test files. A missing arrow does
        not prove two files are unrelated.
      </p>

      <h2>Every file</h2>
      <div className="table-scroll">
        <table className="overview-table">
          <thead>
            <tr>
              <th scope="col">File</th>
              <th scope="col">Classes</th>
              <th scope="col">Functions</th>
              <th scope="col">Calls out</th>
              <th scope="col">Calls in</th>
              <th scope="col">Within the file</th>
            </tr>
          </thead>
          <tbody>
            {overview.files.map((file) => (
              <FileRow key={file.path} file={file} />
            ))}
          </tbody>
        </table>
      </div>
      <LicenseNotice repository={overview.repository} />
    </>
  );
}

function FileRow({ file }) {
  const link = safeGitHubLink(file.link);

  return (
    <tr>
      <th scope="row">
        {link === null ? (
          file.path
        ) : (
          <a href={link} target="_blank" rel="noreferrer">
            {file.path}
          </a>
        )}
      </th>
      <td>{file.classes}</td>
      <td>{file.functions}</td>
      <td>{file.calls_out}</td>
      <td>{file.calls_in}</td>
      <td>{file.calls_within}</td>
    </tr>
  );
}
