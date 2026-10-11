import { useMemo, useState } from "react";
import { connectedFiles, connectionsOf } from "../overview/connections.js";

export default function FileConnections({ links }) {
  const files = useMemo(() => connectedFiles(links), [links]);
  const [selectedPath, setSelectedPath] = useState(files[0]?.path ?? null);

  if (selectedPath === null) {
    return null;
  }

  const { outgoing, incoming } = connectionsOf(selectedPath, links);
  const neighbours = new Set([...outgoing, ...incoming].map((connection) => connection.path));

  return (
    <div className="connections">
      <ul className="connection-files" aria-label="Files that call or are called by another file">
        {files.map((file) => (
          <li key={file.path}>
            <button
              type="button"
              className={neighbours.has(file.path) ? "connection-file is-neighbour" : "connection-file"}
              aria-pressed={file.path === selectedPath}
              onClick={() => setSelectedPath(file.path)}
            >
              <span>{file.path}</span>
              <span>{file.calls}</span>
            </button>
          </li>
        ))}
      </ul>
      <div className="connection-detail" aria-live="polite">
        <h3>{selectedPath}</h3>
        <ConnectionList title="Calls into" arrow="→" connections={outgoing} />
        <ConnectionList title="Called by" arrow="←" connections={incoming} />
      </div>
    </div>
  );
}

function ConnectionList({ title, arrow, connections }) {
  return (
    <div>
      <p className="small-label">{title}</p>
      <ul className="connection-list">
        {connections.length === 0 && <li className="muted">No other file</li>}
        {connections.map((connection) => (
          <li key={connection.path}>
            <span>
              {arrow} {connection.path}
            </span>
            <span>{connection.calls}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
