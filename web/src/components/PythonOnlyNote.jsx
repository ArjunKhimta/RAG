import { describeOtherLanguages } from "../repositories/otherLanguages.js";

export default function PythonOnlyNote({ counts }) {
  const description = describeOtherLanguages(counts);
  if (description === null) {
    return null;
  }
  return (
    <p className="python-only-note">
      Only the Python code was indexed; this repository also has {description}.
    </p>
  );
}
