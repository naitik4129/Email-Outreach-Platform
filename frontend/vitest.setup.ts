import "@testing-library/jest-dom/vitest";

// jsdom has no layout engine. ProseMirror (TipTap) measures ranges/elements when
// it scrolls the caret into view; give it empty geometry instead of crashing.
const emptyRects = () =>
  Object.assign([], { item: () => null }) as unknown as DOMRectList;
const emptyRect = () => new DOMRect(0, 0, 0, 0);

if (typeof Range !== "undefined") {
  Range.prototype.getClientRects ??= emptyRects;
  Range.prototype.getBoundingClientRect ??= emptyRect;
}
if (typeof Element !== "undefined") {
  Element.prototype.getClientRects ??= emptyRects;
}
if (typeof Text !== "undefined") {
  (Text.prototype as unknown as { getClientRects?: () => DOMRectList }).getClientRects ??=
    emptyRects;
}
if (typeof document !== "undefined") {
  document.elementFromPoint ??= () => null;
}
