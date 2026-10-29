// Pure contain-style sizing for the live drawing paper. It only computes CSS
// display dimensions; the canvas backing store remains owned by main.js.
export function containDimensions(artworkWidth, artworkHeight, availableWidth, availableHeight) {
  if (![artworkWidth, artworkHeight, availableWidth, availableHeight].every(Number.isFinite)
      || artworkWidth <= 0 || artworkHeight <= 0 || availableWidth <= 0 || availableHeight <= 0) {
    return { width: 0, height: 0, scale: 0 };
  }
  const scale = Math.min(availableWidth / artworkWidth, availableHeight / artworkHeight);
  return {
    width: artworkWidth * scale,
    height: artworkHeight * scale,
    scale,
  };
}
