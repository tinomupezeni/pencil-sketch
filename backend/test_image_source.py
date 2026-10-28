"""Tests for image_source.py (Phase 7: remote image import / SSRF safety).

External HTTP is always mocked (httpx.MockTransport) or replaced with a
fake DNS resolver -- nothing here depends on live network or live
Pinterest availability, per the Phase 7 instructions. (The real,
unmocked behavior was additionally verified manually against live hosts
during development; that verification is not part of this automated
suite and is reported separately, not claimed here.)
"""

import asyncio
import socket

import cv2
import httpx
import numpy as np
import pytest

import image_source
from image_source import (
    ImageSourceError,
    _is_blocked_ip,
    _looks_like_image,
    _parse_and_validate_url,
    _resolve_and_validate_host,
    classify_url,
    resolve_image_bytes,
)


def _tiny_jpeg_bytes() -> bytes:
    img = np.full((8, 8, 3), 128, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _tiny_png_bytes() -> bytes:
    img = np.full((8, 8, 3), 200, dtype=np.uint8)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def _use_mock_transport(monkeypatch, handler):
    def _build_client():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)

    monkeypatch.setattr(image_source, "_build_safe_client", _build_client)


def _clear_cache():
    image_source._cache.clear()


# --- URL validation / classification -----------------------------------------


def test_rejects_non_https_scheme():
    with pytest.raises(ImageSourceError):
        _parse_and_validate_url("http://example.com/a.jpg")


def test_rejects_embedded_credentials():
    with pytest.raises(ImageSourceError):
        _parse_and_validate_url("https://user:pass@example.com/a.jpg")


def test_rejects_non_standard_port():
    with pytest.raises(ImageSourceError):
        _parse_and_validate_url("https://example.com:8443/a.jpg")


def test_rejects_empty_url():
    with pytest.raises(ImageSourceError):
        _parse_and_validate_url("   ")


def test_classifies_direct_image_url():
    parts = _parse_and_validate_url("https://example.com/photo.jpg")
    assert classify_url(parts) == "direct_image"


def test_classifies_pinterest_pin_url():
    parts = _parse_and_validate_url("https://www.pinterest.com/pin/123456789/")
    assert classify_url(parts) == "pinterest_pin"


def test_classifies_pinterest_short_link():
    parts = _parse_and_validate_url("https://pin.it/abc123")
    assert classify_url(parts) == "pinterest_short_link"


# --- IP blocklist (pure function) --------------------------------------------


@pytest.mark.parametrize(
    "ip_str",
    [
        "127.0.0.1",  # loopback
        "10.0.0.1",  # RFC1918 private
        "172.16.0.5",  # RFC1918 private
        "192.168.1.1",  # RFC1918 private
        "169.254.169.254",  # cloud metadata / link-local
        "0.0.0.0",  # unspecified
        "224.0.0.1",  # multicast
        "::1",  # IPv6 loopback
        "fe80::1",  # IPv6 link-local
        "fc00::1",  # IPv6 unique-local
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.0.0.1",  # IPv4-mapped private
    ],
)
def test_blocks_unsafe_addresses(ip_str):
    import ipaddress

    assert _is_blocked_ip(ipaddress.ip_address(ip_str)) is True


@pytest.mark.parametrize("ip_str", ["8.8.8.8", "1.1.1.1", "2606:4700:10::6814:179a"])
def test_allows_public_addresses(ip_str):
    import ipaddress

    assert _is_blocked_ip(ipaddress.ip_address(ip_str)) is False


# --- DNS resolution validation / rebinding protection ------------------------


class _FakeLoop:
    def __init__(self, infos):
        self._infos = infos

    async def getaddrinfo(self, host, port, type=None):
        if isinstance(self._infos, Exception):
            raise self._infos
        return self._infos


def _patch_loop(monkeypatch, infos):
    fake_loop = _FakeLoop(infos)
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)


@pytest.mark.asyncio
async def test_resolve_rejects_localhost(monkeypatch):
    _patch_loop(monkeypatch, [(socket.AF_INET, None, None, None, ("127.0.0.1", 443))])
    with pytest.raises(ImageSourceError):
        await _resolve_and_validate_host("localhost")


@pytest.mark.asyncio
async def test_resolve_rejects_private_ipv4(monkeypatch):
    _patch_loop(monkeypatch, [(socket.AF_INET, None, None, None, ("10.1.2.3", 443))])
    with pytest.raises(ImageSourceError):
        await _resolve_and_validate_host("internal.example")


@pytest.mark.asyncio
async def test_resolve_rejects_private_ipv6(monkeypatch):
    _patch_loop(monkeypatch, [(socket.AF_INET6, None, None, None, ("fc00::1", 0, 0, 0))])
    with pytest.raises(ImageSourceError):
        await _resolve_and_validate_host("internal-v6.example")


@pytest.mark.asyncio
async def test_resolve_rejects_if_any_resolved_address_is_unsafe(monkeypatch):
    # Classic rebinding/multi-homed scenario: one public address, one
    # private -- must fail closed rather than trusting the public one.
    _patch_loop(
        monkeypatch,
        [
            (socket.AF_INET, None, None, None, ("8.8.8.8", 443)),
            (socket.AF_INET, None, None, None, ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(ImageSourceError):
        await _resolve_and_validate_host("mixed.example")


@pytest.mark.asyncio
async def test_resolve_accepts_public_address(monkeypatch):
    _patch_loop(monkeypatch, [(socket.AF_INET, None, None, None, ("8.8.8.8", 443))])
    ip = await _resolve_and_validate_host("public.example")
    assert ip == "8.8.8.8"


@pytest.mark.asyncio
async def test_resolve_rejects_dns_failure(monkeypatch):
    _patch_loop(monkeypatch, socket.gaierror("name resolution failed"))
    with pytest.raises(ImageSourceError):
        await _resolve_and_validate_host("nonexistent.invalid")


def test_safe_transport_fails_closed_if_httpx_internals_change(monkeypatch):
    class _StubTransport:
        pass  # no `_pool` attribute at all

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda retries=0: _StubTransport())
    with pytest.raises(RuntimeError):
        image_source._make_safe_transport()


# --- end-to-end resolution (mocked transport) ---------------------------------


@pytest.mark.asyncio
async def test_valid_direct_image_url(monkeypatch):
    _clear_cache()

    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=_tiny_jpeg_bytes())

    _use_mock_transport(monkeypatch, handler)
    data, content_type = await resolve_image_bytes("https://example.com/photo.jpg")
    assert content_type == "image/jpeg"
    assert data == _tiny_jpeg_bytes()


@pytest.mark.asyncio
async def test_valid_pinterest_pin_url_resolves_via_oembed(monkeypatch):
    _clear_cache()

    def handler(request):
        if "oembed.json" in str(request.url):
            return httpx.Response(200, json={"thumbnail_url": "https://i.pinimg.com/236x/a.jpg"})
        if request.url.host == "i.pinimg.com":
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=_tiny_jpeg_bytes())
        return httpx.Response(404)

    _use_mock_transport(monkeypatch, handler)
    data, content_type = await resolve_image_bytes("https://www.pinterest.com/pin/123456789/")
    assert content_type == "image/jpeg"
    assert data == _tiny_jpeg_bytes()


@pytest.mark.asyncio
async def test_valid_short_link_redirect_chain(monkeypatch):
    _clear_cache()

    def handler(request):
        host = request.url.host
        if host == "pin.it":
            return httpx.Response(302, headers={"location": "https://www.pinterest.com/pin/555/"})
        if "oembed.json" in str(request.url):
            return httpx.Response(200, json={"thumbnail_url": "https://i.pinimg.com/x.jpg"})
        if host == "i.pinimg.com":
            return httpx.Response(200, headers={"content-type": "image/png"}, content=_tiny_png_bytes())
        return httpx.Response(404)

    _use_mock_transport(monkeypatch, handler)
    data, content_type = await resolve_image_bytes("https://pin.it/abc123")
    assert content_type == "image/png"
    assert data == _tiny_png_bytes()


@pytest.mark.asyncio
async def test_redirect_to_non_https_destination_is_rejected(monkeypatch):
    _clear_cache()

    def handler(request):
        return httpx.Response(302, headers={"location": "http://example.com/evil.jpg"})

    _use_mock_transport(monkeypatch, handler)
    with pytest.raises(ImageSourceError):
        await resolve_image_bytes("https://example.com/redirector")


@pytest.mark.asyncio
async def test_oversized_remote_response_is_rejected(monkeypatch):
    _clear_cache()
    monkeypatch.setattr(image_source, "MAX_DOWNLOAD_BYTES", 10)

    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"x" * 1000)

    _use_mock_transport(monkeypatch, handler)
    with pytest.raises(ImageSourceError) as exc_info:
        await resolve_image_bytes("https://example.com/huge.jpg")
    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_unsupported_content_type_is_rejected(monkeypatch):
    _clear_cache()

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html></html>")

    _use_mock_transport(monkeypatch, handler)
    with pytest.raises(ImageSourceError) as exc_info:
        await resolve_image_bytes("https://example.com/not-an-image")
    assert exc_info.value.status_code == 415


@pytest.mark.asyncio
async def test_non_image_bytes_with_image_mime_type_is_rejected(monkeypatch):
    _clear_cache()

    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"not actually a jpeg")

    _use_mock_transport(monkeypatch, handler)
    with pytest.raises(ImageSourceError) as exc_info:
        await resolve_image_bytes("https://example.com/fake.jpg")
    assert exc_info.value.status_code == 415


@pytest.mark.asyncio
async def test_timeout_is_reported_cleanly(monkeypatch):
    _clear_cache()

    def handler(request):
        raise httpx.ConnectTimeout("timed out")

    _use_mock_transport(monkeypatch, handler)
    with pytest.raises(ImageSourceError) as exc_info:
        await resolve_image_bytes("https://example.com/slow.jpg")
    assert exc_info.value.status_code == 504


@pytest.mark.asyncio
async def test_network_failure_is_reported_cleanly(monkeypatch):
    _clear_cache()

    def handler(request):
        raise httpx.ConnectError("connection refused")

    _use_mock_transport(monkeypatch, handler)
    with pytest.raises(ImageSourceError) as exc_info:
        await resolve_image_bytes("https://example.com/unreachable.jpg")
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_private_or_inaccessible_pin_is_rejected(monkeypatch):
    _clear_cache()

    def handler(request):
        return httpx.Response(200, json={"error": "Url was not found"})

    _use_mock_transport(monkeypatch, handler)
    with pytest.raises(ImageSourceError) as exc_info:
        await resolve_image_bytes("https://www.pinterest.com/pin/999999999/")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_private_or_inaccessible_pin_is_rejected_when_oembed_uses_a_4xx_status(monkeypatch):
    # Regression test: real Pinterest oEmbed returns HTTP 400 (not 200)
    # with a JSON {"error": ...} body for a not-found/private Pin -- this
    # must still be read and reported as "Pin unavailable", not swallowed
    # as a generic "Remote server returned 400" transport failure.
    _clear_cache()

    def handler(request):
        return httpx.Response(400, json={"error": "Url was not found"})

    _use_mock_transport(monkeypatch, handler)
    with pytest.raises(ImageSourceError) as exc_info:
        await resolve_image_bytes("https://www.pinterest.com/pin/1/")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_pin_share_url_with_tracking_params_is_normalized_before_oembed(monkeypatch):
    # Regression test: a pin.it short link commonly redirects to a
    # .../pin/<id>/sent/?invite_code=...&sender=... "share" URL, which real
    # Pinterest oEmbed does NOT recognize -- it must be normalized to the
    # canonical /pin/<id>/ form before the oEmbed call.
    _clear_cache()
    seen_oembed_urls = []

    def handler(request):
        url = str(request.url)
        if "oembed.json" in url:
            seen_oembed_urls.append(url)
            return httpx.Response(200, json={"thumbnail_url": "https://i.pinimg.com/x.jpg"})
        if request.url.host == "i.pinimg.com":
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=_tiny_jpeg_bytes())
        return httpx.Response(404)

    _use_mock_transport(monkeypatch, handler)
    share_url = (
        "https://www.pinterest.com/pin/555913147750607973/sent/"
        "?invite_code=abc123&sender=999&sfo=1"
    )
    data, content_type = await resolve_image_bytes(share_url)
    assert content_type == "image/jpeg"
    assert len(seen_oembed_urls) == 1
    assert "sent" not in seen_oembed_urls[0]
    assert "invite_code" not in seen_oembed_urls[0]
    assert "555913147750607973" in seen_oembed_urls[0]


@pytest.mark.asyncio
async def test_unsupported_video_pin_is_rejected(monkeypatch):
    _clear_cache()

    def handler(request):
        # A real oEmbed payload shape but with no thumbnail_url -- the
        # documented best-effort signal we use for "can't resolve this
        # automatically" (e.g. a video Pin).
        return httpx.Response(200, json={"version": "1.0", "type": "video", "title": "a video pin"})

    _use_mock_transport(monkeypatch, handler)
    with pytest.raises(ImageSourceError) as exc_info:
        await resolve_image_bytes("https://www.pinterest.com/pin/888888888/")
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_cache_avoids_a_second_fetch_for_the_same_url(monkeypatch):
    _clear_cache()
    call_count = {"n": 0}

    def handler(request):
        call_count["n"] += 1
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=_tiny_jpeg_bytes())

    _use_mock_transport(monkeypatch, handler)
    await resolve_image_bytes("https://example.com/cached.jpg")
    await resolve_image_bytes("https://example.com/cached.jpg")
    assert call_count["n"] == 1


def test_looks_like_image_recognizes_real_signatures():
    assert _looks_like_image(_tiny_jpeg_bytes())
    assert _looks_like_image(_tiny_png_bytes())
    assert not _looks_like_image(b"not an image at all")
