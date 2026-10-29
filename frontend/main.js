// UI orchestration only: DOM wiring, mode switching, logging/status text.
// All actual playback logic lives in js/player.js (+ js/renderer.js,
// js/pencil-tip.js, js/timeline.js, js/playback-state.js, js/clock.js,
// js/geometry.js), which this file treats as a black box driven by
// callbacks. Phase 8 adds: Setup/Drawing mode switching, auto-hiding
// controls, glanceable phase/progress text, Wake Lock, and IndexedDB
// session recovery -- each as a small testable pure module (idle-state.js,
// phase-label.js, wake-lock.js, session-policy.js, session-store.js) that
// this file wires to the DOM.
import { Player } from "./js/player.js";
import { GraphiteRenderer } from "./js/renderer.js";
import { PencilTip } from "./js/pencil-tip.js";
import { States } from "./js/playback-state.js";
import {
  Source,
  LinkPreviewState,
  createImageSourceState,
  isStartDrawingVisible,
  canStartDrawing,
} from "./js/image-source-state.js";
import { phaseLabelForStage, formatProgressLine } from "./js/phase-label.js";
import { createIdleState, recordActivity, setFocusWithinControls, shouldShowControls } from "./js/idle-state.js";
import { WakeLockController } from "./js/wake-lock.js";
import { createSessionStore } from "./js/session-store.js";
import { ageDescription } from "./js/session-policy.js";
import { containDimensions } from "./js/fit.js";

const appEl = document.getElementById("app");
const setupStudio = document.getElementById("setup-studio");
const drawingStudio = document.getElementById("drawing-studio");

const fileInput = document.getElementById("file-input");
const durationSelect = document.getElementById("duration");
const styleFaithfulBtn = document.getElementById("style-faithful");
const styleGraphiteBtn = document.getElementById("style-graphite");
const startDrawingBtn = document.getElementById("start-drawing");
const setupStatusEl = document.getElementById("setup-status");

const tabUpload = document.getElementById("tab-upload");
const tabLink = document.getElementById("tab-link");
const panelUpload = document.getElementById("source-upload");
const panelLink = document.getElementById("source-link");
const uploadDropzone = document.getElementById("upload-dropzone");
const uploadFilenameEl = document.getElementById("upload-filename");
const imageUrlInput = document.getElementById("image-url-input");
const previewUrlBtn = document.getElementById("preview-url");
const linkStatusEl = document.getElementById("link-status");
const selectionPreviewImage = document.getElementById("selection-preview-image");
const previewEmptyState = document.getElementById("preview-empty-state");

const resumeBanner = document.getElementById("resume-banner");
const resumeText = document.getElementById("resume-text");
const resumeAcceptBtn = document.getElementById("resume-accept");
const resumeDismissBtn = document.getElementById("resume-dismiss");

const paperInner = document.getElementById("paper-inner");
const artWorkspace = document.querySelector(".art-workspace");
const canvas = document.getElementById("canvas");
const tipCanvas = document.getElementById("tip-canvas");
const phaseProgressLineEl = document.getElementById("phase-progress-line");
const pacingBadgeEl = document.getElementById("pacing-badge");
const progressFillEl = document.getElementById("progress-fill");
const timeRemainingEl = document.getElementById("time-remaining");
const drawingControls = document.getElementById("drawing-controls");
const newDrawingBtn = document.getElementById("new-drawing");
const focusToggleBtn = document.getElementById("focus-toggle");
const replayBtn = document.getElementById("replay");
const pauseResumeBtn = document.getElementById("pause-resume");
const saveArtworkBtn = document.getElementById("save-artwork");
const fullscreenBtn = document.getElementById("fullscreen");

let currentPlan = null;
let currentDurationMs = 0;
let lastProgress = { elapsedMs: 0, totalMs: 0 };
let currentPhaseLabel = "Beginning";

// Phase 9: which of Atelier's two artistic styles to request. Faithful
// Pencil (the default) preserves source color accents; Graphite Study is
// a deliberate monochrome interpretation. Both reuse the same planner and
// playback architecture -- this only changes what the backend generates.
let currentStyle = "faithful";

function setStyle(style) {
  currentStyle = style;
  styleFaithfulBtn.classList.toggle("active", style === "faithful");
  styleFaithfulBtn.setAttribute("aria-checked", String(style === "faithful"));
  styleGraphiteBtn.classList.toggle("active", style === "graphite");
  styleGraphiteBtn.setAttribute("aria-checked", String(style === "graphite"));
}

styleFaithfulBtn.addEventListener("click", () => setStyle("faithful"));
styleGraphiteBtn.addEventListener("click", () => setStyle("graphite"));

function updateGlanceText() {
  const pct =
    lastProgress.totalMs === 0 ? 0 : Math.min(100, Math.round((lastProgress.elapsedMs / lastProgress.totalMs) * 100));
  phaseProgressLineEl.textContent = formatProgressLine(currentPhaseLabel, pct);
  progressFillEl.style.width = `${pct}%`;
}

function setMode(mode) {
  appEl.dataset.mode = mode;
  setupStudio.hidden = mode !== "setup";
  drawingStudio.hidden = mode !== "drawing";
  fullscreenBtn.hidden = mode !== "drawing";
}

function setSetupStatus(text, isError) {
  setupStatusEl.textContent = text || "";
  setupStatusEl.classList.toggle("error", !!isError);
}

function formatDuration(ms) {
  const totalSeconds = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

// --- Canvas sizing: CSS display size vs. backing-store pixel size -------
//
// The wrapper's aspect-ratio (set here, from the plan's own width/height)
// is what controls *displayed* size and keeps proportions correct at any
// container size, including fullscreen -- never cropped or stretched. The
// canvas's own .width/.height attributes instead control backing-store
// resolution, scaled by devicePixelRatio (capped at 2x so a very
// high-density display doesn't needlessly balloon memory) for crisp lines.
// Setting .width/.height necessarily clears the bitmap, so this is only
// ever called right before player.load()/start() -- never on a window
// resize or fullscreen toggle, which must NOT reset the drawing.
const MAX_DEVICE_PIXEL_RATIO = 2;

function fitPaperToWorkspace() {
  if (!currentPlan || !artWorkspace.clientWidth || !artWorkspace.clientHeight) return;
  const workspaceStyle = getComputedStyle(artWorkspace);
  const availableWidth = artWorkspace.clientWidth
    - parseFloat(workspaceStyle.paddingLeft)
    - parseFloat(workspaceStyle.paddingRight);
  const availableHeight = artWorkspace.clientHeight
    - parseFloat(workspaceStyle.paddingTop)
    - parseFloat(workspaceStyle.paddingBottom);
  const fitted = containDimensions(
    currentPlan.width,
    currentPlan.height,
    availableWidth,
    availableHeight,
  );
  paperInner.style.width = `${fitted.width}px`;
  paperInner.style.height = `${fitted.height}px`;
}

function resizeCanvases(width, height) {
  const dpr = Math.min(MAX_DEVICE_PIXEL_RATIO, window.devicePixelRatio || 1);
  paperInner.style.aspectRatio = `${width} / ${height}`;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  tipCanvas.width = canvas.width;
  tipCanvas.height = canvas.height;
  canvas.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
  tipCanvas.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
  fitPaperToWorkspace();
}

// CSS-only resizing keeps the backing store and renderer coordinates intact.
// A ResizeObserver also catches fullscreen transitions without clearing the
// existing drawing bitmap.
const workspaceResizeObserver = new ResizeObserver(() => fitPaperToWorkspace());
workspaceResizeObserver.observe(artWorkspace);

// One shared seed drives every bit of controlled variation (motion jitter,
// travel bow, evaluation-pause placement, pressure jitter) so a given
// image always plays back identically.
const PLAYBACK_SEED = 1;

const player = new Player({
  graphite: new GraphiteRenderer(canvas.getContext("2d"), { seed: PLAYBACK_SEED }),
  tip: new PencilTip(tipCanvas.getContext("2d")),
  seed: PLAYBACK_SEED,
});

// --- Wake Lock (section 8): requested while actively drawing, released on
// pause/completion/error. Browsers that don't support it, or that deny the
// request, degrade silently -- this never blocks or interrupts playback.
const wakeLock = new WakeLockController();

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    wakeLock.reacquireIfWanted();
  }
});

// --- Session recovery (section 8): IndexedDB only (never localStorage --
// a DrawingPlan can be tens of megabytes). Bounded to one record, saved on
// a throttle while playing and on page hide, offered back as a dismissible
// banner in Setup Studio. Never on by default for a *finished* drawing --
// there's nothing left to resume into.
const sessionStore = createSessionStore();
const SESSION_SAVE_INTERVAL_MS = 5000;
let lastSessionSaveAt = 0;

async function saveSessionSnapshot() {
  if (!currentPlan || lastProgress.totalMs === 0) return;
  await sessionStore.save({
    plan: currentPlan,
    elapsedMs: lastProgress.elapsedMs,
    targetDurationMs: currentDurationMs,
    savedAt: Date.now(),
  });
}

async function maybeOfferResume() {
  const saved = await sessionStore.loadIfRecoverable(Date.now());
  if (!saved) return;
  resumeText.textContent = `Resume your drawing from ${ageDescription(saved, Date.now())}?`;
  resumeBanner.hidden = false;
  resumeAcceptBtn.onclick = () => {
    resumeBanner.hidden = true;
    currentPlan = saved.plan;
    currentDurationMs = saved.targetDurationMs;
    resizeCanvases(currentPlan.width, currentPlan.height);
    setMode("drawing");
    currentPhaseLabel = "Resuming";
    lastProgress = { elapsedMs: saved.elapsedMs, totalMs: saved.targetDurationMs };
    updateGlanceText();
    updatePacingBadge();
    player.load(currentPlan, currentDurationMs);
    player.start({ resumeAtMs: saved.elapsedMs });
    wakeLock.request();
  };
  resumeDismissBtn.onclick = () => {
    resumeBanner.hidden = true;
    sessionStore.discard();
  };
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") saveSessionSnapshot();
});
window.addEventListener("pagehide", () => saveSessionSnapshot());

// --- Auto-hiding controls (section 6) ------------------------------------

const idleState = createIdleState({ idleTimeoutMs: 2600 });

function refreshControlsVisibility() {
  const visible = shouldShowControls(idleState, performance.now());
  drawingControls.classList.toggle("idle", !visible);
}

function noteActivity() {
  recordActivity(idleState, performance.now());
  refreshControlsVisibility();
}

for (const evt of ["mousemove", "mousedown", "touchstart", "keydown"]) {
  drawingStudio.addEventListener(evt, noteActivity, { passive: true });
}
drawingControls.addEventListener("focusin", () => {
  setFocusWithinControls(idleState, true);
  refreshControlsVisibility();
});
drawingControls.addEventListener("focusout", () => {
  setFocusWithinControls(idleState, false);
  refreshControlsVisibility();
});
setInterval(refreshControlsVisibility, 400);

// --- Player wiring --------------------------------------------------------

player.onStateChange = (state) => {
  replayBtn.disabled = !currentPlan;
  pauseResumeBtn.hidden = state === States.COMPLETED;
  pauseResumeBtn.disabled = ![States.PLAYING, States.PAUSED].includes(state);
  pauseResumeBtn.textContent = state === States.PAUSED ? "▶" : "⏸";
  pauseResumeBtn.title = state === States.PAUSED ? "Resume" : "Pause";
  saveArtworkBtn.hidden = state !== States.COMPLETED;

  if (state === States.PLAYING) {
    wakeLock.request();
    noteActivity();
  } else if (state === States.PAUSED) {
    wakeLock.release();
  } else if (state === States.COMPLETED) {
    wakeLock.release();
    currentPhaseLabel = "Drawing complete";
    lastProgress = { ...lastProgress, elapsedMs: lastProgress.totalMs };
    updateGlanceText();
    timeRemainingEl.textContent = "";
    sessionStore.discard();
  } else if (state === States.ERROR) {
    wakeLock.release();
    setMode("setup");
    setSetupStatus(player.lastError, true);
  }
};

player.onProgress = ({ elapsedMs, totalMs }) => {
  lastProgress = { elapsedMs, totalMs };
  const remaining = Math.max(0, totalMs - elapsedMs);
  if (player.state !== States.COMPLETED) {
    timeRemainingEl.textContent = `${formatDuration(remaining)} remaining`;
  }
  updateGlanceText();

  const now = Date.now();
  if (now - lastSessionSaveAt > SESSION_SAVE_INTERVAL_MS) {
    lastSessionSaveAt = now;
    saveSessionSnapshot();
  }
};

player.onPhaseChange = (stage) => {
  const stageNames = currentPlan && currentPlan.metadata ? currentPlan.metadata.stageNames : null;
  currentPhaseLabel = phaseLabelForStage(stageNames, stage);
  updateGlanceText();
};

// --- Image source: Upload vs Paste Image Link -------------------------------
// Both sources now feed one shared preview area and one shared "Begin
// Drawing" action (Phase 8.2) -- picking an image never starts drawing by
// itself; the user always explicitly commits via Begin Drawing.

const sourceState = createImageSourceState();
let uploadPreviewObjectUrl = null;
let linkPreviewObjectUrl = null;

function refreshSourceUI() {
  const uploadActive = sourceState.source === Source.UPLOAD;
  tabUpload.classList.toggle("active", uploadActive);
  tabUpload.setAttribute("aria-selected", String(uploadActive));
  tabLink.classList.toggle("active", !uploadActive);
  tabLink.setAttribute("aria-selected", String(!uploadActive));
  panelUpload.hidden = !uploadActive;
  panelLink.hidden = uploadActive;

  startDrawingBtn.hidden = !isStartDrawingVisible(sourceState);
  startDrawingBtn.disabled = !canStartDrawing(sourceState);

  const activePreviewUrl = uploadActive ? uploadPreviewObjectUrl : linkPreviewObjectUrl;
  if (activePreviewUrl) {
    selectionPreviewImage.src = activePreviewUrl;
    selectionPreviewImage.hidden = false;
    previewEmptyState.hidden = true;
  } else {
    selectionPreviewImage.hidden = true;
    previewEmptyState.hidden = false;
  }
}

function setLinkStatus(text, isError) {
  linkStatusEl.textContent = text;
  linkStatusEl.classList.toggle("error", !!isError);
}

function handleUploadFileSelected(file) {
  if (!file) return;
  sourceState.uploadFile = file;
  if (uploadPreviewObjectUrl) URL.revokeObjectURL(uploadPreviewObjectUrl);
  uploadPreviewObjectUrl = URL.createObjectURL(file);
  uploadFilenameEl.textContent = file.name;
  refreshSourceUI();
}

tabUpload.addEventListener("click", () => {
  sourceState.source = Source.UPLOAD;
  refreshSourceUI();
});

tabLink.addEventListener("click", () => {
  sourceState.source = Source.LINK;
  refreshSourceUI();
});

fileInput.addEventListener("change", () => {
  handleUploadFileSelected(fileInput.files[0]);
});

// Keyboard accessibility: the visible control is a <label>, which doesn't
// natively forward Enter/Space to its associated input the way a real
// <button> or <input> would.
uploadDropzone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fileInput.click();
  }
});

for (const evt of ["dragover", "dragenter"]) {
  uploadDropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    uploadDropzone.classList.add("dragover");
  });
}
for (const evt of ["dragleave", "drop"]) {
  uploadDropzone.addEventListener(evt, () => uploadDropzone.classList.remove("dragover"));
}
uploadDropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  handleUploadFileSelected(file);
});

previewUrlBtn.addEventListener("click", async () => {
  const url = imageUrlInput.value.trim();
  sourceState.linkUrl = url;
  if (!url) {
    sourceState.linkPreview = LinkPreviewState.ERROR;
    setLinkStatus("Paste an image URL first.", true);
    linkPreviewObjectUrl = null;
    refreshSourceUI();
    return;
  }

  sourceState.linkPreview = LinkPreviewState.LOADING;
  setLinkStatus("Loading preview…", false);
  linkPreviewObjectUrl = null;
  previewUrlBtn.disabled = true;
  refreshSourceUI();

  try {
    // Fetches through our own backend (/api/preview), never by pointing
    // <img src> directly at the pasted URL -- the backend does the actual
    // validated, SSRF-safe fetch; we only ever display what it hands back.
    const res = await fetch(`/api/preview?url=${encodeURIComponent(url)}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Could not load that image (${res.status})`);
    }
    const blob = await res.blob();
    if (linkPreviewObjectUrl) URL.revokeObjectURL(linkPreviewObjectUrl);
    linkPreviewObjectUrl = URL.createObjectURL(blob);
    sourceState.linkPreview = LinkPreviewState.SUCCESS;
    setLinkStatus("Preview loaded.", false);
  } catch (err) {
    sourceState.linkPreview = LinkPreviewState.ERROR;
    sourceState.linkError = err.message;
    setLinkStatus(err.message, true);
  } finally {
    previewUrlBtn.disabled = false;
    refreshSourceUI();
  }
});

imageUrlInput.addEventListener("input", () => {
  if (sourceState.linkUrl !== imageUrlInput.value.trim()) {
    sourceState.linkPreview = LinkPreviewState.IDLE;
    linkPreviewObjectUrl = null;
    refreshSourceUI();
  }
});

startDrawingBtn.addEventListener("click", () => {
  if (!canStartDrawing(sourceState)) return;
  const durationSeconds = targetDurationMs() / 1000;
  if (sourceState.source === Source.UPLOAD) {
    const formData = new FormData();
    formData.append("file", sourceState.uploadFile);
    formData.append("style", currentStyle);
    formData.append("durationSeconds", String(durationSeconds));
    runPipeline(() => fetch("/api/sketch", { method: "POST", body: formData }), "uploading image…");
  } else {
    runPipeline(
      () =>
        fetch("/api/sketch-url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: sourceState.linkUrl, style: currentStyle, durationSeconds }),
        }),
      "fetching image…"
    );
  }
});

refreshSourceUI();

// --- shared pipeline: fetch a DrawingPlan (upload or URL) and play it -------

async function runPipeline(makeRequest, logMessage) {
  setSetupStatus("Processing image…", false);
  console.debug(logMessage);
  player.setLoading();
  startDrawingBtn.disabled = true;

  try {
    const res = await makeRequest();
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Server error (${res.status})`);
    }
    currentPlan = await res.json();
    setSetupStatus("", false);
    beginPlayback();
  } catch (err) {
    player.setError(err.message);
    refreshSourceUI(); // restores Begin Drawing's enabled state after a failure
  }
}

function targetDurationMs() {
  const durationMinutes = Math.max(0.1, parseFloat(durationSelect.value) || 120);
  return durationMinutes * 60 * 1000;
}

function updatePacingBadge() {
  // Phase 10 (section 3): the backend labels a plan "timelapse" when its
  // (possibly detail-budgeted) stroke set still plays faster than natural
  // pencil pace -- shown here rather than quietly pretending a sped-up
  // session is a natural-paced one.
  const pacing = currentPlan && currentPlan.metadata ? currentPlan.metadata.durationPlanning?.pacing : null;
  pacingBadgeEl.hidden = pacing !== "timelapse";
}

function beginPlayback() {
  currentDurationMs = targetDurationMs();
  resizeCanvases(currentPlan.width, currentPlan.height);
  setMode("drawing");
  currentPhaseLabel = "Beginning";
  lastProgress = { elapsedMs: 0, totalMs: currentDurationMs };
  updateGlanceText();
  updatePacingBadge();
  timeRemainingEl.textContent = `pacing to ${formatDuration(currentDurationMs)}`;
  player.load(currentPlan, currentDurationMs);
  player.start();
}

replayBtn.addEventListener("click", () => {
  if (currentPlan) beginPlayback();
});

pauseResumeBtn.addEventListener("click", () => {
  if (player.state === States.PLAYING) {
    player.pause();
  } else if (player.state === States.PAUSED) {
    player.resume();
    wakeLock.request();
  }
});

newDrawingBtn.addEventListener("click", () => {
  if (player.state === States.PLAYING) player.pause();
  wakeLock.release();
  sessionStore.discard();
  setMode("setup");
  setSetupStatus("", false);
});

focusToggleBtn.addEventListener("click", () => {
  const nextFocus = appEl.dataset.focus !== "true";
  appEl.dataset.focus = String(nextFocus);
  focusToggleBtn.title = nextFocus ? "Exit focus mode" : "Focus mode";
  focusToggleBtn.setAttribute("aria-label", focusToggleBtn.title);
});

fullscreenBtn.addEventListener("click", () => {
  if (!document.fullscreenElement) {
    // Fullscreens the whole drawing studio (artwork + session bar +
    // controls), not just the canvas -- see the CSS comment on
    // .drawing-studio:fullscreen for why that distinction matters.
    drawingStudio.requestFullscreen().catch(() => {
      // Fullscreen can be denied by the browser/OS -- fail silently and
      // quietly; it's not essential to the experience.
    });
  } else {
    document.exitFullscreen();
  }
});

saveArtworkBtn.addEventListener("click", () => {
  // Exports the graphite canvas alone -- the pencil tip lives on a
  // separate overlay canvas and is never composited into this export, and
  // the progress/controls are plain DOM, not canvas content.
  const link = document.createElement("a");
  link.download = "pencil-sketch.png";
  link.href = canvas.toDataURL("image/png");
  link.click();
});

// --- Initial state ----------------------------------------------------------

setMode("setup");
maybeOfferResume();

// Test-only escape hatch so visual/browser verification can reach the
// "completed" state without a real multi-minute wait. Has no effect on
// normal use (nothing calls it in the app itself) and does nothing unless
// a plan is already loaded and playing.
window.__debugForceComplete = () => {
  if (!player.plan) return;
  const farFuture = performance.now() + player.totalMs + 10000;
  for (let i = 0; i < 20000 && player.state === States.PLAYING; i++) {
    player._tick(farFuture);
  }
};

// Same idea, but seeks to an arbitrary fraction of the timeline instead of
// always running to completion -- used to capture 25/50/75% progress
// screenshots without waiting in real time.
window.__debugSeekToFraction = (fraction) => {
  if (!player.plan) return;
  const target = performance.now() + player.totalMs * Math.min(1, Math.max(0, fraction));
  for (let i = 0; i < 20000 && player.state === States.PLAYING; i++) {
    const before = player._actionCursor;
    player._tick(target);
    if (player._actionCursor === before) break; // caught up to this target
  }
  // Each manual _tick call above re-arms the player's own real
  // requestAnimationFrame loop (see Player._scheduleNext). Left alone, that
  // real frame fires moments later with the browser's *actual* elapsed
  // time (still only milliseconds into real playback) and overwrites the
  // progress this seek just produced with a tiny, stale value -- a race
  // that only this synchronous debug jump can trigger, never real
  // (non-seeking) playback. Canceling it here freezes the player exactly
  // at the requested fraction for inspection/screenshots.
  if (player.state === States.PLAYING) player._cancelScheduled();
};

// Test-only: exposes the just-loaded plan's metadata for browser-driven
// verification scripts (e.g. reading Phase 10's durationPlanning decision)
// without needing a separate network round-trip.
Object.defineProperty(window, "__debugLastPlanMeta", {
  get: () => (currentPlan ? currentPlan.metadata : null),
});

window.__debugPlayerState = () => ({
  totalMs: player.totalMs,
  lastProgress,
  currentDurationMs,
  actionCursor: player._actionCursor,
  entryCount: player.entries.length,
});
