// Pure state for the "Upload Image" vs "Paste Image Link" switch. No DOM
// here -- see main.js for the actual wiring -- so switching sources and
// the preview loading/success/error states are unit-testable without a
// browser.
export const Source = Object.freeze({ UPLOAD: "upload", LINK: "link" });

export const LinkPreviewState = Object.freeze({
  IDLE: "idle",
  LOADING: "loading",
  SUCCESS: "success",
  ERROR: "error",
});

export function createImageSourceState() {
  return {
    source: Source.UPLOAD,
    uploadFile: null,
    linkUrl: "",
    linkPreview: LinkPreviewState.IDLE,
    linkError: null,
  };
}

/** Phase 8.2: "Begin Drawing" is always visible (a single, unambiguous
 * primary action for either source), just disabled until ready -- see
 * canStartDrawing. Kept as a named function (rather than inlined `true`)
 * so the UI's reason for showing/hiding stays declarative and testable. */
export function isStartDrawingVisible(_state) {
  return true;
}

export function canStartDrawing(state) {
  if (state.source === Source.UPLOAD) return !!state.uploadFile;
  if (state.source === Source.LINK) return state.linkPreview === LinkPreviewState.SUCCESS && !!state.linkUrl;
  return false;
}
