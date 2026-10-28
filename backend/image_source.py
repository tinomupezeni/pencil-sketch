"""
Resolves a user-supplied remote image URL (a direct image link or a
Pinterest Pin link) into verified, decodable image bytes -- or raises a
clear, user-safe ImageSourceError.

Kept deliberately separate from image analysis (sketch.py) and planning
(planner.py): this module's only job is "URL in, bytes out, or a clear
error", so future image providers (beyond Pinterest/direct URLs) can be
added here without touching the endpoint or the drawing pipeline.

SSRF mitigation (see _SafeAsyncNetworkBackend): every outbound connection
this module makes -- including every redirect hop -- resolves DNS itself,
validates every returned address against a fail-closed "is this globally
routable and non-multicast" check, and then connects directly to that one
validated IP (TLS SNI/certificate verification still uses the original
hostname, via httpcore's separate server_hostname handling). This defeats
both naive SSRF (connect straight to 127.0.0.1/private ranges) and DNS
rebinding (resolve-then-reconnect races), because the IP that gets
connected to is the exact IP that was validated, not a fresh lookup made
moments later by some other layer.

What this module deliberately does NOT do:
  - It is not a general-purpose URL proxy: only a fixed set of operations
    (resolve a short link, call Pinterest's own oEmbed endpoint, fetch one
    image) are possible, never an arbitrary pass-through request.
  - It never uses the Pinterest API (which requires app review and OAuth
    for most endpoints) -- it uses Pinterest's public, unauthenticated
    oEmbed endpoint (https://www.pinterest.com/oembed.json), which is
    Pinterest's own officially documented embedding mechanism for public
    Pins. Private, deleted, or otherwise inaccessible Pins correctly fail
    here (oEmbed returns an error for them) -- this is not a bypass of any
    access control, it only ever sees what Pinterest already serves
    unauthenticated to any embedding site.
  - It does not promise every Pinterest Pin resolves: video Pins and any
    Pin oEmbed doesn't return a thumbnail_url for are explicitly rejected
    with a message pointing the user at Pinterest's own "Download image"
    option instead.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import time
from collections import OrderedDict
from urllib.parse import quote, urlsplit

import httpx
from httpcore._backends.anyio import AnyIOBackend

# --- configurable safety budgets --------------------------------------------

ALLOWED_SCHEMES = {"https"}
CONNECT_TIMEOUT_SECONDS = 8.0
READ_TIMEOUT_SECONDS = 12.0
MAX_REDIRECTS = 5
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # matches main.py's upload budget
DOWNLOAD_CHUNK_BYTES = 64 * 1024
MAX_OEMBED_RESPONSE_BYTES = 1 * 1024 * 1024
ALLOWED_IMAGE_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/bmp",
}
# A few real image file-signature prefixes, checked before the full decode
# (cv2.imdecode, in sketch.py) -- not the security boundary by itself (the
# full decode is), but gives a clearer, faster error for obviously-wrong
# content than "could not decode image".
_IMAGE_MAGIC_BYTES = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"GIF87a",
    b"GIF89a",
    b"BM",  # BMP
    b"RIFF",  # WEBP (RIFF....WEBP)
)

MAX_CONCURRENT_REMOTE_FETCHES = 4  # bounds concurrency; does not touch CV/planning concurrency

_PINTEREST_PIN_HOSTS = {"www.pinterest.com", "pinterest.com"}
_PINTEREST_SHORT_HOSTS = {"pin.it"}
_PINTEREST_CDN_SUFFIX = ".pinimg.com"
_PIN_PATH_RE = re.compile(r"^/pin/\d+/?")

OEMBED_ENDPOINT = "https://www.pinterest.com/oembed.json"
_REQUEST_HEADERS = {"User-Agent": "pencil-sketch-image-import/1.0"}

_CACHE_TTL_SECONDS = 60
_CACHE_MAX_ENTRIES = 16


class ImageSourceError(Exception):
    """A user-facing, safely-messaged failure. `status_code` is the HTTP
    status the endpoint should return; `message` never contains internal
    details (stack traces, file paths, library internals)."""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


_RESOLVE_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_REMOTE_FETCHES)

# Bounded, short-lived, in-process only -- never persisted to disk. Lets a
# preview immediately followed by "Start Drawing" reuse the same bytes
# instead of downloading/decoding the remote image twice.
_cache: "OrderedDict[str, tuple[float, bytes, str]]" = OrderedDict()


def _cache_get(key: str) -> tuple[bytes, str] | None:
    entry = _cache.get(key)
    if not entry:
        return None
    ts, data, content_type = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        del _cache[key]
        return None
    _cache.move_to_end(key)
    return data, content_type


def _cache_put(key: str, data: bytes, content_type: str) -> None:
    _cache[key] = (time.time(), data, content_type)
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)


# --- URL validation / classification -----------------------------------------


def _parse_and_validate_url(raw_url: str):
    raw_url = (raw_url or "").strip()
    if not raw_url:
        raise ImageSourceError("Please enter an image URL.", status_code=400)
    try:
        parts = urlsplit(raw_url)
    except ValueError as exc:
        raise ImageSourceError("That doesn't look like a valid URL.", status_code=400) from exc

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise ImageSourceError("Only https:// URLs are supported.", status_code=400)
    if parts.username or parts.password:
        raise ImageSourceError("URLs with embedded credentials are not supported.", status_code=400)
    if not parts.hostname:
        raise ImageSourceError("That doesn't look like a valid URL.", status_code=400)
    if parts.port not in (None, 443):
        raise ImageSourceError("Only standard HTTPS (port 443) is supported.", status_code=400)
    return parts


def classify_url(parts) -> str:
    """Returns "pinterest_short_link" | "pinterest_pin" | "direct_image".
    Anything not recognized as Pinterest is treated as a direct-image
    fetch attempt (and will simply fail later if it isn't one).
    """
    host = parts.hostname.lower()
    if host in _PINTEREST_SHORT_HOSTS:
        return "pinterest_short_link"
    if host in _PINTEREST_PIN_HOSTS and _PIN_PATH_RE.match(parts.path):
        return "pinterest_pin"
    return "direct_image"


# --- SSRF-safe transport -----------------------------------------------------


def _is_blocked_ip(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    # Fail-closed default-deny: only a globally-routable, non-multicast
    # address is allowed. `is_global` already excludes private/loopback/
    # link-local/reserved/unspecified ranges for both IPv4 and IPv6
    # (including IPv4-mapped IPv6 addresses); multicast is checked
    # explicitly since some multicast ranges report is_global=True.
    return (not ip.is_global) or ip.is_multicast


async def _resolve_and_validate_host(hostname: str) -> str:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ImageSourceError(f"Could not resolve host: {hostname}", status_code=400) from exc
    if not infos:
        raise ImageSourceError(f"Could not resolve host: {hostname}", status_code=400)

    resolved: list[tuple[int, str]] = []
    for family, _, _, _, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if _is_blocked_ip(ip):
            raise ImageSourceError(f"{hostname} is not an allowed destination.", status_code=400)
        resolved.append((family, sockaddr[0]))

    # Prefer IPv4 for determinism; any validated address is equally safe.
    for family, ip_str in resolved:
        if family == socket.AF_INET:
            return ip_str
    return resolved[0][1]


class _SafeAsyncNetworkBackend(AnyIOBackend):
    """Resolves DNS itself, validates every address, and connects to the
    exact validated IP -- see module docstring. TLS hostname verification
    is unaffected: httpcore calls `stream.start_tls(..., server_hostname=)`
    with the original hostname regardless of which IP the stream itself
    connected to.
    """

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        validated_ip = await _resolve_and_validate_host(host)
        return await super().connect_tcp(
            validated_ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )


def _make_safe_transport() -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport(retries=0)
    pool = getattr(transport, "_pool", None)
    if pool is None or not hasattr(pool, "_network_backend"):
        # Fail closed: if a future httpx/httpcore upgrade changes this
        # private shape, refuse to fetch remote URLs rather than silently
        # fetching them without SSRF protection.
        raise RuntimeError("SSRF-safe transport could not be installed; refusing remote fetches")
    pool._network_backend = _SafeAsyncNetworkBackend()
    return transport


def _build_safe_client() -> httpx.AsyncClient:
    timeout = httpx.Timeout(
        connect=CONNECT_TIMEOUT_SECONDS,
        read=READ_TIMEOUT_SECONDS,
        write=READ_TIMEOUT_SECONDS,
        pool=CONNECT_TIMEOUT_SECONDS,
    )
    return httpx.AsyncClient(
        transport=_make_safe_transport(),
        timeout=timeout,
        follow_redirects=False,  # redirects are validated and followed manually, one hop at a time
        trust_env=False,  # ignore HTTP_PROXY/etc. env vars -- don't let deployment config reroute us
    )


# --- bounded fetch with manual, validated redirects --------------------------


async def _safe_get(
    url: str,
    *,
    client: httpx.AsyncClient,
    max_bytes: int,
    accept_content_types: set[str] | None,
    allow_error_status: bool = False,
) -> tuple[bytes, str]:
    """`allow_error_status=True` is for endpoints (like Pinterest's oEmbed)
    that use a 4xx status *with a meaningful JSON body* as their normal
    "not found" signal -- the caller inspects the body itself rather than
    this function treating every 4xx as fatal before the body can be read.
    """
    current_url = url
    for _ in range(MAX_REDIRECTS + 1):
        _parse_and_validate_url(current_url)
        try:
            async with client.stream("GET", current_url, headers=_REQUEST_HEADERS) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise ImageSourceError("Remote server redirected without a destination.", status_code=502)
                    current_url = str(httpx.URL(current_url).join(location))
                    continue

                if resp.status_code >= 400 and not (allow_error_status and resp.status_code < 500):
                    raise ImageSourceError(f"Remote server returned {resp.status_code}.", status_code=502)

                content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if accept_content_types and content_type not in accept_content_types:
                    raise ImageSourceError(
                        f"Unsupported content type: {content_type or 'unknown'}", status_code=415
                    )

                content_length = resp.headers.get("content-length")
                if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                    raise ImageSourceError("The remote file is too large.", status_code=413)

                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes(DOWNLOAD_CHUNK_BYTES):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ImageSourceError("The remote file is too large.", status_code=413)
                    chunks.append(chunk)
                return b"".join(chunks), content_type
        except httpx.TimeoutException as exc:
            raise ImageSourceError("The remote server took too long to respond.", status_code=504) from exc
        except httpx.TransportError as exc:
            raise ImageSourceError("Could not reach the remote server.", status_code=502) from exc

    raise ImageSourceError("Too many redirects.", status_code=400)


async def _resolve_redirect_chain(url: str, client: httpx.AsyncClient) -> str:
    """Follows redirects (each hop re-validated) without downloading any
    response body, returning the final landed URL."""
    current_url = url
    for _ in range(MAX_REDIRECTS + 1):
        _parse_and_validate_url(current_url)
        try:
            async with client.stream("GET", current_url, headers=_REQUEST_HEADERS) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise ImageSourceError("Redirect missing a destination.", status_code=502)
                    current_url = str(httpx.URL(current_url).join(location))
                    continue
                return current_url
        except httpx.TimeoutException as exc:
            raise ImageSourceError("The remote server took too long to respond.", status_code=504) from exc
        except httpx.TransportError as exc:
            raise ImageSourceError("Could not reach the remote server.", status_code=502) from exc
    raise ImageSourceError("Too many redirects.", status_code=400)


def _canonical_pin_url(parts) -> str:
    """Normalizes to Pinterest's canonical https://<host>/pin/<id>/ form.
    Share/referral variants (e.g. the /sent/ path with invite_code/sender
    tracking query params that a pin.it short link commonly redirects to)
    are not recognized by the oEmbed endpoint -- only the bare pin URL is.
    """
    match = _PIN_PATH_RE.match(parts.path)
    pin_id = re.search(r"\d+", match.group(0)).group(0)
    return f"https://{parts.hostname}/pin/{pin_id}/"


async def _resolve_pin_thumbnail(pin_url: str, client: httpx.AsyncClient) -> str:
    """Pinterest's own public oEmbed endpoint -- no auth, no API key, the
    same mechanism any site uses to embed a public Pin. Returns a usable
    image URL (a thumbnail -- see module docstring on resolution limits).
    """
    oembed_url = f"{OEMBED_ENDPOINT}?url={quote(pin_url, safe='')}"
    # oEmbed uses a 4xx status *with* a JSON {"error": ...} body as its
    # normal "Pin not found/private/inaccessible" signal -- read the body
    # regardless of status so that case is handled below, not swallowed as
    # a generic transport failure.
    data, _content_type = await _safe_get(
        oembed_url,
        client=client,
        max_bytes=MAX_OEMBED_RESPONSE_BYTES,
        accept_content_types=None,
        allow_error_status=True,
    )
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ImageSourceError("Pinterest did not return a usable response for this Pin.", status_code=502) from exc

    if "error" in payload:
        raise ImageSourceError(
            "This Pin could not be found, or is private/inaccessible. "
            "Try Pinterest's \"Download image\" option and upload the file instead.",
            status_code=404,
        )
    thumbnail_url = payload.get("thumbnail_url")
    if not thumbnail_url:
        raise ImageSourceError(
            "This Pin's image couldn't be resolved automatically (it may be a video Pin). "
            "Try Pinterest's \"Download image\" option and upload the file instead.",
            status_code=422,
        )
    return thumbnail_url


def _looks_like_image(data: bytes) -> bool:
    return any(data.startswith(sig) for sig in _IMAGE_MAGIC_BYTES)


async def resolve_image_bytes(url: str) -> tuple[bytes, str]:
    """Public entry point: URL in, (image_bytes, content_type) out, or a
    raised ImageSourceError with a safe, user-facing message. Cached
    bounded/short-lived (see _cache_*) so a preview immediately followed by
    "Start Drawing" doesn't fetch the remote image twice.
    """
    cache_key = (url or "").strip()
    cached = _cache_get(cache_key)
    if cached:
        return cached

    async with _RESOLVE_SEMAPHORE:
        parts = _parse_and_validate_url(url)
        client = _build_safe_client()
        try:
            kind = classify_url(parts)
            target_url = url

            if kind == "pinterest_short_link":
                target_url = await _resolve_redirect_chain(url, client)
                kind = classify_url(_parse_and_validate_url(target_url))

            if kind == "pinterest_pin":
                canonical_url = _canonical_pin_url(_parse_and_validate_url(target_url))
                image_url = await _resolve_pin_thumbnail(canonical_url, client)
                data, content_type = await _safe_get(
                    image_url, client=client, max_bytes=MAX_DOWNLOAD_BYTES, accept_content_types=ALLOWED_IMAGE_CONTENT_TYPES
                )
            else:
                data, content_type = await _safe_get(
                    target_url, client=client, max_bytes=MAX_DOWNLOAD_BYTES, accept_content_types=ALLOWED_IMAGE_CONTENT_TYPES
                )
        finally:
            await client.aclose()

    if not _looks_like_image(data):
        raise ImageSourceError("The remote file does not look like a supported image.", status_code=415)

    _cache_put(cache_key, data, content_type)
    return data, content_type
