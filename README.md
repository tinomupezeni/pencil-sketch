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

### Docker (recommended)

Start the app in the background:

```bash
docker compose up -d --build
```

Then open http://127.0.0.1:8000. The Compose service uses Docker's
`unless-stopped` restart policy, so it comes back automatically after a
container failure or computer restart, as long as Docker Desktop/Docker
Engine is configured to start on boot. Check it with:

```bash
docker compose ps
docker compose logs -f
```

Stop it when needed:

```bash
docker compose down
```

On Linux, enable Docker at boot once with:

```bash
sudo systemctl enable --now docker
```

On Docker Desktop, enable “Start Docker Desktop when you sign in” in
Settings > General.

### Local Python

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Run the backend tests from `backend/` with `pytest -q`. Run the frontend
tests from `frontend/` with `npm test`. The backend requirements are pinned in
`backend/requirements.txt`; use the same Python environment for the server
and tests.

Then open http://127.0.0.1:8000 and upload an image.

## Next steps / ideas

- Tune Canny thresholds and hatch band thresholds per-image (auto-expose
  based on histogram) instead of fixed constants.
- Smooth/resample contours before hatching so curves don't look faceted.
- Add a "paper texture" canvas background and slightly randomize stroke
  jitter for a more organic pencil look.
- Let the user pick a style preset (loose sketch vs. tight line art vs.
  heavy shading).
