import { Link } from "react-router";

const NOT_FOUND_STATUS = 404;

export default function LoadFailure({ error, notFoundTitle, backTo, backLabel }) {
  if (error.status === NOT_FOUND_STATUS) {
    return (
      <section>
        <h1>{notFoundTitle}</h1>
        <p>
          It may have been deleted. <Link to={backTo}>{backLabel}</Link>
        </p>
      </section>
    );
  }
  return <p role="alert">Could not load this page: {error.message}</p>;
}
