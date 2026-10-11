const SECONDS_PER_MINUTE = 60;

const SECONDS_PER_HOUR = 3600;

export function formatLocalTime(isoTime) {
  return new Date(isoTime).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function describeWait(seconds) {
  if (seconds < SECONDS_PER_MINUTE) {
    return "less than a minute";
  }
  if (seconds < SECONDS_PER_HOUR) {
    const minutes = Math.round(seconds / SECONDS_PER_MINUTE);
    return minutes === 1 ? "about 1 minute" : `about ${minutes} minutes`;
  }
  const hours = Math.round(seconds / SECONDS_PER_HOUR);
  return hours === 1 ? "about 1 hour" : `about ${hours} hours`;
}

export function messageWithWait(error) {
  if (error.retryAfterSeconds === null || error.retryAfterSeconds === undefined) {
    return error.message;
  }
  return `${error.message} (in ${describeWait(error.retryAfterSeconds)}).`;
}
