export const Timing = Object.freeze({
  SHOWN_ANSWER: "time to a shown answer",
  SHOWN_DIAGRAM: "time to a shown diagram",
  LOAD_MERMAID: "load Mermaid",
  DRAW_DIAGRAM: "draw diagram",
});

const START_MARK_SUFFIX = " start";

function startMarkOf(name) {
  return `${name}${START_MARK_SUFFIX}`;
}

function userTimingAvailable() {
  return (
    typeof performance !== "undefined" &&
    typeof performance.mark === "function" &&
    typeof performance.measure === "function"
  );
}

export function startTiming(name) {
  if (!userTimingAvailable()) {
    return;
  }
  performance.clearMarks(startMarkOf(name));
  performance.mark(startMarkOf(name));
}

export function finishTiming(name) {
  if (!userTimingAvailable()) {
    return;
  }
  const startMark = startMarkOf(name);
  if (performance.getEntriesByName(startMark, "mark").length === 0) {
    return;
  }
  performance.measure(name, startMark);
  performance.clearMarks(startMark);
}

export function cancelTiming(name) {
  if (!userTimingAvailable()) {
    return;
  }
  performance.clearMarks(startMarkOf(name));
}

export async function timed(name, work) {
  startTiming(name);
  try {
    const result = await work();
    finishTiming(name);
    return result;
  } catch (error) {
    cancelTiming(name);
    throw error;
  }
}
