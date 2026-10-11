export function describeOtherLanguages(counts) {
  if (!Array.isArray(counts) || counts.length === 0) {
    return null;
  }
  const phrases = counts.map(
    (count) => `${count.files.toLocaleString("en-US")} ${count.language} ${count.files === 1 ? "file" : "files"}`,
  );
  if (phrases.length === 1) {
    return phrases[0];
  }
  return `${phrases.slice(0, -1).join(", ")} and ${phrases.at(-1)}`;
}
