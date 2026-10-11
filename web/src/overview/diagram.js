export const MAX_DIAGRAM_FILES = 12;

const UNSAFE_LABEL_CHARACTERS = /[^A-Za-z0-9_.\-/ ]/g;

export function safeLabel(text) {
  return text.replace(UNSAFE_LABEL_CHARACTERS, "_");
}

export function folderOf(path) {
  const lastSlash = path.lastIndexOf("/");
  return lastSlash === -1 ? "" : path.slice(0, lastSlash);
}

export function fileNameOf(path) {
  return path.slice(path.lastIndexOf("/") + 1);
}

function connectionsOf(file) {
  return file.calls_in + file.calls_out;
}

function byConnectionsThenPath(first, second) {
  const difference = connectionsOf(second) - connectionsOf(first);
  return difference !== 0 ? difference : first.path.localeCompare(second.path);
}

export function mostConnectedFiles(files, maxFiles = MAX_DIAGRAM_FILES) {
  return files
    .filter((file) => connectionsOf(file) > 0)
    .sort(byConnectionsThenPath)
    .slice(0, maxFiles);
}

export function buildDiagram(overview, maxFiles = MAX_DIAGRAM_FILES) {
  const shownFiles = mostConnectedFiles(overview.files, maxFiles);
  const nodeIds = new Map(shownFiles.map((file, index) => [file.path, `f${index}`]));
  const shownLinks = overview.links.filter(
    (link) => nodeIds.has(link.from) && nodeIds.has(link.to),
  );

  const filesByFolder = new Map();
  for (const file of shownFiles) {
    const folder = folderOf(file.path);
    if (!filesByFolder.has(folder)) {
      filesByFolder.set(folder, []);
    }
    filesByFolder.get(folder).push(file);
  }

  const lines = ["flowchart LR"];
  const folders = [...filesByFolder.keys()].sort();
  folders.forEach((folder, folderIndex) => {
    const nodes = filesByFolder
      .get(folder)
      .map((file) => `${nodeIds.get(file.path)}["${safeLabel(fileNameOf(file.path))}"]`);
    if (folder === "") {
      lines.push(...nodes.map((node) => `  ${node}`));
      return;
    }
    lines.push(`  subgraph g${folderIndex}["${safeLabel(folder)}"]`);
    lines.push(...nodes.map((node) => `    ${node}`));
    lines.push("  end");
  });
  for (const link of shownLinks) {
    lines.push(`  ${nodeIds.get(link.from)} -->|${link.calls}| ${nodeIds.get(link.to)}`);
  }

  return {
    source: lines.join("\n"),
    shownFileCount: shownFiles.length,
    shownLinkCount: shownLinks.length,
    connectedFileCount: overview.files.filter((file) => connectionsOf(file) > 0).length,
  };
}
