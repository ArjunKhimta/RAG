import { useState } from "react";

export default function ConfirmButton({ label, question, confirmLabel, onConfirm, disabled }) {
  const [confirming, setConfirming] = useState(false);

  if (!confirming) {
    return (
      <button
        type="button"
        className="button-plain"
        onClick={() => setConfirming(true)}
        disabled={disabled}
      >
        {label}
      </button>
    );
  }
  return (
    <span className="removal-confirmation">
      <span>{question}</span>
      <button type="button" className="button button-danger" onClick={onConfirm} disabled={disabled}>
        {confirmLabel}
      </button>
      <button
        type="button"
        className="button-plain"
        onClick={() => setConfirming(false)}
        disabled={disabled}
      >
        Cancel
      </button>
    </span>
  );
}
