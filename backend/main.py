from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from image_source import ImageSourceError, resolve_image_bytes
from planner import build_drawing_plan
from sketch import VALID_STYLES, image_to_strokes

app = FastAPI(title="Pencil Sketch")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Resource safety budget (Phase 6, section 5): bounds how much of an
# upload we'll buffer into memory before even attempting to decode it,
# independent of the client-supplied Content-Length/content-type (which
# are not trustworthy). The image-dimension and stroke-count budgets live
# in sketch.py, closer to where they're enforced. Remote-fetch budgets
# (Phase 7) live in image_source.py, next to the SSRF protection they're
# part of.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB
_READ_CHUNK_BYTES = 1024 * 1024


async def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Reads `file` incrementally, aborting as soon as the cap is exceeded
    -- never buffers an unbounded upload into memory first."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Upload exceeds the {max_bytes // (1024 * 1024)}MB limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _build_plan_from_image_bytes(data: bytes, style: str, duration_seconds: float | None) -> dict:
    """The one shared image-to-drawing pipeline: both /api/sketch (upload)
    and /api/sketch-url (remote import) funnel through this exact function,
    so stroke generation, planning, and serialization are never duplicated
    between the two entry points (Phase 7, section 2).

    `style` selects between Atelier's two artistic styles (Phase 9):
    "faithful" (default) preserves source color accents, "graphite" is a
    deliberately monochrome interpretation. Both reuse the exact same
    planner and playback architecture -- style only changes which strokes
    sketch.py generates, never anything downstream.

    `duration_seconds` (Phase 10, corrected by 10.1) is the session length
    the client intends to *play this plan over* -- it never changes which
    strokes are in the plan (see planner.classify_duration_pacing), only
    whether the plan's metadata reports the implied pacing as "natural"
    or "timelapse" for the frontend's UI label. `None` reports "natural"
    with no other effect.

    This CV + planning work is synchronous and runs on the event loop
    exactly as it did before this phase -- deliberately unchanged (Phase 7,
    section 6): the new remote-fetch code is async/non-blocking, but this
    call is not threadpool-offloaded here, matching existing behavior
    rather than silently changing its concurrency characteristics.
    """
    try:
        raw = image_to_strokes(data, style=style)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    plan = build_drawing_plan(raw["width"], raw["height"], raw["strokes"], target_duration_seconds=duration_seconds)
    dumped = plan.model_dump(mode="json")

    # Pydantic's model_dump includes every field, so a monochrome stroke's
    # optional `color` would otherwise serialize as a literal `"color":
    # null` on every single stroke -- real, measured payload bloat across
    # tens of thousands of strokes, and ambiguous for any client checking
    # "does this stroke have a color" by key presence rather than
    # truthiness. Dropping it when unset restores the exact pre-Phase-9
    # shape for monochrome strokes (key absent, not null).
    for stroke in dumped["strokes"]:
        if stroke.get("color") is None:
            stroke.pop("color", None)

    return dumped


# The UI's duration selector (Phase 10, section 3) offers 2 minutes to 4
# hours -- validated here so an out-of-range value fails loudly (400)
# rather than silently producing a nonsensical detail budget.
MIN_DURATION_SECONDS = 60.0
MAX_DURATION_SECONDS = 4 * 3600.0


def _validate_style(style: str) -> str:
    if style not in VALID_STYLES:
        raise HTTPException(status_code=400, detail=f"Unknown style: {style!r} (expected one of {VALID_STYLES})")
    return style


def _validate_duration_seconds(duration_seconds: float | None) -> float | None:
    if duration_seconds is None:
        return None
    if not (MIN_DURATION_SECONDS <= duration_seconds <= MAX_DURATION_SECONDS):
        raise HTTPException(
            status_code=400,
            detail=f"durationSeconds must be between {MIN_DURATION_SECONDS:.0f} and {MAX_DURATION_SECONDS:.0f}",
        )
    return duration_seconds


@app.post("/api/sketch")
async def sketch(
    file: UploadFile = File(...),
    style: str = Form("faithful"),
    duration_seconds: float | None = Form(None, alias="durationSeconds"),
) -> dict:
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Upload an image file")
    style = _validate_style(style)
    duration_seconds = _validate_duration_seconds(duration_seconds)

    data = await _read_capped(file, MAX_UPLOAD_BYTES)
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    return _build_plan_from_image_bytes(data, style, duration_seconds)


class SketchUrlRequest(BaseModel):
    url: str
    style: str = "faithful"
    durationSeconds: float | None = None

    @field_validator("style")
    @classmethod
    def _style_is_valid(cls, v: str) -> str:
        if v not in VALID_STYLES:
            raise ValueError(f"Unknown style: {v!r} (expected one of {VALID_STYLES})")
        return v

    @field_validator("durationSeconds")
    @classmethod
    def _duration_is_in_range(cls, v: float | None) -> float | None:
        if v is not None and not (MIN_DURATION_SECONDS <= v <= MAX_DURATION_SECONDS):
            raise ValueError(f"durationSeconds must be between {MIN_DURATION_SECONDS:.0f} and {MAX_DURATION_SECONDS:.0f}")
        return v


@app.post("/api/sketch-url")
async def sketch_url(payload: SketchUrlRequest) -> dict:
    try:
        data, _content_type = await resolve_image_bytes(payload.url)
    except ImageSourceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return _build_plan_from_image_bytes(data, payload.style, payload.durationSeconds)


@app.get("/api/preview")
async def preview(url: str = Query(...)) -> Response:
    """Lets the frontend show a preview of a pasted URL *through our own
    validated fetch path* -- never by pointing an <img> tag directly at an
    untrusted third-party URL (which would bypass all of the above
    validation and leak the viewer's IP/cookies to an arbitrary host).
    Shares resolve_image_bytes's short-lived cache with /api/sketch-url, so
    previewing then immediately drawing doesn't fetch the image twice.
    """
    try:
        data, content_type = await resolve_image_bytes(url)
    except ImageSourceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return Response(content=data, media_type=content_type or "application/octet-stream")


app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")
