export function connectedFiles(links) {
  const totals = new Map();
  for (const link of links) {
    totals.set(link.from, (totals.get(link.from) ?? 0) + link.calls);
    totals.set(link.to, (totals.get(link.to) ?? 0) + link.calls);
  }
  return [...totals.entries()]
    .map(([path, calls]) => ({ path, calls }))
    .sort(byCallsThenPath);
}

export function connectionsOf(path, links) {
  const outgoing = links
    .filter((link) => link.from === path)
    .map((link) => ({ path: link.to, calls: link.calls }))
    .sort(byCallsThenPath);
  const incoming = links
    .filter((link) => link.to === path)
    .map((link) => ({ path: link.from, calls: link.calls }))
    .sort(byCallsThenPath);
  return { outgoing, incoming };
}

function byCallsThenPath(first, second) {
  return second.calls - first.calls || first.path.localeCompare(second.path);
}
