const SVG_TYPE = "image/svg+xml";

export function sizedSvg(svgText) {
  const document = new DOMParser().parseFromString(svgText, SVG_TYPE);
  const svg = document.documentElement;
  const viewBox = (svg.getAttribute("viewBox") ?? "").trim().split(/[\s,]+/).map(Number);
  if (viewBox.length === 4 && viewBox.every(Number.isFinite)) {
    svg.setAttribute("width", String(Math.ceil(viewBox[2])));
    svg.setAttribute("height", String(Math.ceil(viewBox[3])));
  }
  svg.removeAttribute("style");
  return new XMLSerializer().serializeToString(svg);
}

export function svgImageUrl(svgText) {
  return URL.createObjectURL(new Blob([sizedSvg(svgText)], { type: SVG_TYPE }));
}

export function releaseImageUrl(url) {
  URL.revokeObjectURL(url);
}
