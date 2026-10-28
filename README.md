# Pencil Sketch

Upload an image, get back a pencil-style sketch that draws itself on screen
stroke by stroke.

## How it works

1. `backend/sketch.py` turns an uploaded image into an ordered list of
   strokes using OpenCV:
   - **line strokes**: traced contours from Canny edge detection (the
     outline work).
   - **hatch strokes**: short parallel segments inside tone bands,
     denser and cross-hatched in darker areas (the shading).
   - Strokes are ordered light-hatching → outlines → dark-hatching, closer
     to how a sketch is actually built up.
2. `backend/main.py` is a small FastAPI app exposing `POST /api/sketch`
   (multipart image upload → JSON `{width, height, strokes}`) and serving
   the frontend as static files.
3. `frontend/` is a vanilla HTML/Canvas page that fetches the stroke JSON
   and animates it with `requestAnimationFrame`, drawing a few points per
   frame per stroke so the sketch visibly builds up. A speed slider
   controls how many points are drawn per frame.

## Run it

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Then open http://127.0.0.1:8000 and upload an image.

## Next steps / ideas

- Tune Canny thresholds and hatch band thresholds per-image (auto-expose
  based on histogram) instead of fixed constants.
- Smooth/resample contours before hatching so curves don't look faceted.
- Add a "paper texture" canvas background and slightly randomize stroke
  jitter for a more organic pencil look.
- Let the user pick a style preset (loose sketch vs. tight line art vs.
  heavy shading).
