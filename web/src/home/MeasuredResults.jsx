import { useState } from "react";
import { MEASURED_RESULTS, SearchSetup } from "./results.js";

export default function MeasuredResults() {
  const [setupName, setSetupName] = useState(SearchSetup.VECTOR);
  const setup = MEASURED_RESULTS.setups[setupName];

  return (
    <div>
      <div className="segmented" role="group" aria-label="Search setup">
        {Object.entries(MEASURED_RESULTS.setups).map(([name, item]) => (
          <button
            type="button"
            key={name}
            aria-pressed={name === setupName}
            onClick={() => setSetupName(name)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <dl className="figure-list">
        {setup.figures.map((figure) => (
          <div key={figure.label}>
            <dt>{figure.label}</dt>
            <dd>{figure.value}</dd>
          </div>
        ))}
      </dl>
      <p className="muted">{setup.note}</p>
    </div>
  );
}
