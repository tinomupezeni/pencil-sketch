"""Endpoint tests for POST /api/sketch, now returning a DrawingPlan."""

import cv2
import httpx
import numpy as np
from fastapi.testclient import TestClient

import image_source
import main
from main import app
from plan_models import DrawingPlan, check_plan_invariants

client = TestClient(app)


def _synthetic_png_bytes(size: int = 64) -> bytes:
    img = np.tile(np.linspace(220, 255, size, dtype=np.uint8), (size, 1))
    img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    img[size // 4 : size // 2, size // 4 : size // 2] = (30, 30, 30)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_valid_image_returns_a_valid_drawing_plan():
    response = client.post(
        "/api/sketch",
        files={"file": ("test.png", _synthetic_png_bytes(), "image/png")},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["schemaVersion"]
    assert body["width"] > 0 and body["height"] > 0
    assert "strokes" in body and "actions" in body and "metadata" in body

    # Round-trip through the contract model: this re-runs every field and
    # whole-plan validator, equivalent to re-checking invariants independently
    # of whatever validation already ran inside the handler.
    plan = DrawingPlan.model_validate(body)
    assert check_plan_invariants(plan) == []


def test_backward_compatible_stroke_fields_present():
    # The existing frontend (frontend/main.js) only reads these four fields
    # per stroke, plus top-level width/height -- confirm they still exist
    # and are shaped the same way regardless of the new annotation fields.
    response = client.post(
        "/api/sketch",
        files={"file": ("test.png", _synthetic_png_bytes(), "image/png")},
    )
    body = response.json()
    assert body["strokes"], "expected at least one stroke from a synthetic image with a hard edge"
    for s in body["strokes"]:
        assert isinstance(s["points"], list) and len(s["points"]) >= 2
        assert isinstance(s["width"], (int, float))
        assert isinstance(s["shade"], (int, float))
        assert s["kind"] in ("line", "hatch")


def test_non_image_upload_is_rejected():
    response = client.post(
        "/api/sketch",
        files={"file": ("test.txt", b"not an image", "text/plain")},
    )
    assert response.status_code == 400


def test_corrupt_image_bytes_are_rejected():
    response = client.post(
        "/api/sketch",
        files={"file": ("test.png", b"\x89PNG not actually valid", "image/png")},
    )
    assert response.status_code == 400


def test_empty_upload_is_rejected():
    response = client.post(
        "/api/sketch",
        files={"file": ("test.png", b"", "image/png")},
    )
    assert response.status_code == 400


# --- resource safety budgets (Phase 6, section 5) ---------------------------


def test_upload_over_the_size_budget_is_rejected(monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1024)  # 1KB, force the cap to trigger
    oversized = b"\x00" * 2048
    response = client.post(
        "/api/sketch",
        files={"file": ("test.png", oversized, "image/png")},
    )
    assert response.status_code == 413


def test_upload_within_the_size_budget_is_not_rejected_by_the_cap(monkeypatch):
    # Small enough to pass the size cap, but still not a real image -- it
    # should fail decoding (400), never the size cap (413).
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 1024 * 1024)
    response = client.post(
        "/api/sketch",
        files={"file": ("test.png", b"not a real image", "image/png")},
    )
    assert response.status_code == 400


# --- remote image import (Phase 7) -------------------------------------------


def _mock_remote_image(monkeypatch, data=None, content_type="image/png"):
    data = data if data is not None else _synthetic_png_bytes()

    def handler(request):
        return httpx.Response(200, headers={"content-type": content_type}, content=data)

    def _build_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    monkeypatch.setattr(image_source, "_build_safe_client", _build_client)
    image_source._cache.clear()
    return data


def test_sketch_url_returns_a_valid_drawing_plan(monkeypatch):
    _mock_remote_image(monkeypatch)
    response = client.post("/api/sketch-url", json={"url": "https://example.com/photo.png"})
    assert response.status_code == 200
    body = response.json()
    plan = DrawingPlan.model_validate(body)
    assert check_plan_invariants(plan) == []


def test_sketch_url_rejects_invalid_url():
    response = client.post("/api/sketch-url", json={"url": "not a url"})
    assert response.status_code == 400


def test_sketch_url_rejects_non_https():
    response = client.post("/api/sketch-url", json={"url": "http://example.com/photo.png"})
    assert response.status_code == 400


def test_sketch_url_never_leaks_internal_error_details(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    def _build_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    monkeypatch.setattr(image_source, "_build_safe_client", _build_client)
    image_source._cache.clear()
    response = client.post("/api/sketch-url", json={"url": "https://example.com/unreachable.jpg"})
    assert response.status_code == 502
    assert "Traceback" not in response.text
    assert "httpx" not in response.text.lower()


def test_preview_endpoint_proxies_the_validated_image(monkeypatch):
    data = _mock_remote_image(monkeypatch, content_type="image/png")
    response = client.get("/api/preview", params={"url": "https://example.com/photo.png"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == data


def test_preview_and_sketch_url_share_the_fetch_cache(monkeypatch):
    call_count = {"n": 0}

    def handler(request):
        call_count["n"] += 1
        return httpx.Response(200, headers={"content-type": "image/png"}, content=_synthetic_png_bytes())

    def _build_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    monkeypatch.setattr(image_source, "_build_safe_client", _build_client)
    image_source._cache.clear()

    url = "https://example.com/shared.png"
    assert client.get("/api/preview", params={"url": url}).status_code == 200
    assert client.post("/api/sketch-url", json={"url": url}).status_code == 200
    assert call_count["n"] == 1, "the second request should reuse the cached download"


# --- Phase 9: artistic styles -------------------------------------------------


def test_sketch_endpoint_accepts_a_style_field_and_omits_color_key_when_monochrome():
    response = client.post(
        "/api/sketch",
        files={"file": ("test.png", _synthetic_png_bytes(), "image/png")},
        data={"style": "graphite"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["strokes"], "expected at least one stroke"
    # Regression test: Pydantic's model_dump used to serialize every
    # stroke's optional `color` field as a literal `"color": null`, not
    # omit it -- real payload bloat, and ambiguous for a client checking
    # "does this stroke have a color" by key presence. Graphite style
    # never assigns a color, so no stroke should carry the key at all.
    assert all("color" not in s for s in body["strokes"])


def test_sketch_endpoint_rejects_an_unknown_style():
    response = client.post(
        "/api/sketch",
        files={"file": ("test.png", _synthetic_png_bytes(), "image/png")},
        data={"style": "bogus"},
    )
    assert response.status_code == 400


def test_sketch_url_endpoint_defaults_to_faithful_style_and_rejects_unknown_styles(monkeypatch):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=_synthetic_png_bytes())

    def _build_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    monkeypatch.setattr(image_source, "_build_safe_client", _build_client)
    image_source._cache.clear()

    response = client.post("/api/sketch-url", json={"url": "https://example.com/a.png", "style": "bogus"})
    assert response.status_code == 422  # pydantic request-model validation


def test_sketch_endpoint_faithful_style_can_include_a_color_key_when_present():
    # Uses the real color-accent fixture (via direct pipeline call) to
    # confirm the key survives the full HTTP round-trip when it *should*
    # be present, not just that it's correctly absent when it shouldn't be.
    import cv2
    import numpy as np

    size = 160
    img = np.full((size, size, 3), 238, dtype=np.uint8)
    subject = slice(size // 6, size - size // 6)
    img[subject, subject] = (70, 68, 66)
    accent = slice(size // 3, size // 3 + size // 6)
    img[accent, accent] = (45, 40, 160)
    ok, buf = cv2.imencode(".png", img)
    assert ok

    response = client.post(
        "/api/sketch",
        files={"file": ("test.png", buf.tobytes(), "image/png")},
        data={"style": "faithful"},
    )
    assert response.status_code == 200
    body = response.json()
    colored = [s for s in body["strokes"] if "color" in s]
    assert colored
    for s in colored:
        assert s["color"].startswith("#") and len(s["color"]) == 7


# --- Phase 10: duration-aware planning ----------------------------------------


def test_sketch_endpoint_accepts_a_duration_and_applies_a_detail_budget():
    response = client.post(
        "/api/sketch",
        files={"file": ("test.png", _synthetic_png_bytes(), "image/png")},
        data={"durationSeconds": "60"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["durationPlanning"]["requested"] is True


def test_sketch_endpoint_without_a_duration_keeps_pre_phase_10_behavior():
    response = client.post(
        "/api/sketch",
        files={"file": ("test.png", _synthetic_png_bytes(), "image/png")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["durationPlanning"]["requested"] is False


def test_sketch_endpoint_rejects_a_duration_outside_the_2min_to_4hr_range():
    too_short = client.post(
        "/api/sketch",
        files={"file": ("test.png", _synthetic_png_bytes(), "image/png")},
        data={"durationSeconds": "1"},
    )
    assert too_short.status_code == 400

    too_long = client.post(
        "/api/sketch",
        files={"file": ("test.png", _synthetic_png_bytes(), "image/png")},
        data={"durationSeconds": str(100 * 3600)},
    )
    assert too_long.status_code == 400


def test_sketch_url_endpoint_rejects_a_duration_outside_the_2min_to_4hr_range(monkeypatch):
    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=_synthetic_png_bytes())

    def _build_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    monkeypatch.setattr(image_source, "_build_safe_client", _build_client)
    image_source._cache.clear()

    response = client.post(
        "/api/sketch-url", json={"url": "https://example.com/a.png", "durationSeconds": 1}
    )
    assert response.status_code == 422
