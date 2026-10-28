import test from "node:test";
import assert from "node:assert/strict";
import {
  Source,
  LinkPreviewState,
  createImageSourceState,
  isStartDrawingVisible,
  canStartDrawing,
} from "../js/image-source-state.js";

test("defaults to upload source, Begin Drawing visible but disabled until a file is chosen", () => {
  const state = createImageSourceState();
  assert.equal(state.source, Source.UPLOAD);
  assert.equal(isStartDrawingVisible(state), true);
  assert.equal(canStartDrawing(state), false);
});

test("choosing an upload file enables Begin Drawing", () => {
  const state = createImageSourceState();
  state.uploadFile = { name: "photo.jpg" };
  assert.equal(canStartDrawing(state), true);
});

test("switching to link source stays visible, disabled until a preview succeeds", () => {
  const state = createImageSourceState();
  state.source = Source.LINK;
  assert.equal(isStartDrawingVisible(state), true);
  assert.equal(canStartDrawing(state), false, "no preview attempted yet");

  state.linkPreview = LinkPreviewState.LOADING;
  assert.equal(canStartDrawing(state), false, "still loading");

  state.linkPreview = LinkPreviewState.ERROR;
  state.linkError = "Invalid URL";
  assert.equal(canStartDrawing(state), false, "preview failed");

  state.linkPreview = LinkPreviewState.SUCCESS;
  state.linkUrl = "https://example.com/a.jpg";
  assert.equal(canStartDrawing(state), true, "preview succeeded and a URL is set");
});

test("switching back to upload re-gates on the upload file, independent of link state", () => {
  const state = createImageSourceState();
  state.source = Source.LINK;
  state.linkPreview = LinkPreviewState.SUCCESS;
  state.linkUrl = "https://example.com/a.jpg";
  assert.equal(canStartDrawing(state), true);

  state.source = Source.UPLOAD;
  assert.equal(isStartDrawingVisible(state), true);
  assert.equal(canStartDrawing(state), false, "no upload file chosen yet");

  state.uploadFile = { name: "photo.jpg" };
  assert.equal(canStartDrawing(state), true);
});

test("a successful link preview with no URL set is not enough to start drawing", () => {
  const state = createImageSourceState();
  state.source = Source.LINK;
  state.linkPreview = LinkPreviewState.SUCCESS;
  state.linkUrl = "";
  assert.equal(canStartDrawing(state), false);
});
