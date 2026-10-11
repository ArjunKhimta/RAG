import { useCallback } from "react";
import { Link } from "react-router";
import { fetchRepository } from "../api/repositories.js";
import { useApiResource } from "../hooks/useApiResource.js";
import { LinkStatus } from "../repositories/linkStatus.js";
import LoadFailure from "./LoadFailure.jsx";

export default function ReadyRepository({ linkId, children }) {
  const load = useCallback(() => fetchRepository(linkId), [linkId]);
  const { data: link, error } = useApiResource(load);

  if (error !== null) {
    return (
      <LoadFailure
        error={error}
        notFoundTitle="Repository not found"
        backTo="/repositories"
        backLabel="See your repositories"
      />
    );
  }
  if (link === null) {
    return <p>Loading the repository…</p>;
  }
  if (link.status !== LinkStatus.READY) {
    return (
      <section>
        <h1>
          {link.repository} {link.version}
        </h1>
        <p>
          This repository is not ready yet. <Link to="/repositories">See its progress</Link>
        </p>
      </section>
    );
  }
  return children(link);
}
