"""Safe raster-image ingestion and pluggable image generation.

The presentation model refers to user files through opaque ``asset://`` IDs.
Only this module may turn those IDs into bytes. Arbitrary local paths are never
accepted from model output.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import os
import socket
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Protocol
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

if TYPE_CHECKING:
    from odforge.ir import ImageSpec


MAX_IMAGE_BYTES = 8 * 1024 * 1024
_ALLOWED_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
}


class MediaError(ValueError):
    """Raised when an image source is unsafe or invalid."""


@dataclass(frozen=True)
class AssetBlob:
    """Validated raster bytes ready to embed into an ODF package."""

    data: bytes
    mime_type: str
    extension: str
    width: int
    height: int


class ImageProvider(Protocol):
    """Minimal adapter interface for an external image generator."""

    def generate(self, prompt: str) -> AssetBlob:
        """Generate one image for ``prompt`` or raise :class:`MediaError`."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return (width, height) if width and height else None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            break
        segment_length = struct.unpack(">H", data[offset : offset + 2])[0]
        if segment_length < 2 or offset + segment_length > len(data):
            break
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if segment_length < 7:
                break
            height, width = struct.unpack(
                ">HH", data[offset + 3 : offset + 7]
            )
            return (width, height) if width and height else None
        offset += segment_length
    return None


def validate_image_bytes(
    data: bytes, declared_mime: str | None = None
) -> AssetBlob:
    """Validate size, signature and dimensions of PNG/JPEG bytes."""

    if not data:
        raise MediaError("image is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise MediaError(
            f"image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MiB limit"
        )

    dimensions = _png_dimensions(data)
    mime_type = "image/png" if dimensions else ""
    if dimensions is None:
        dimensions = _jpeg_dimensions(data)
        mime_type = "image/jpeg" if dimensions else ""
    if dimensions is None:
        raise MediaError("only valid PNG and JPEG images are supported")

    normalized_declared = (
        declared_mime.split(";", 1)[0].strip().lower() if declared_mime else ""
    )
    if normalized_declared and normalized_declared not in _ALLOWED_MIME:
        raise MediaError(f"unsupported image content type: {normalized_declared}")
    if normalized_declared and normalized_declared != mime_type:
        raise MediaError(
            f"image signature is {mime_type}, not {normalized_declared}"
        )
    width, height = dimensions
    return AssetBlob(
        data=data,
        mime_type=mime_type,
        extension=_ALLOWED_MIME[mime_type],
        width=width,
        height=height,
    )


def decode_data_uri(value: str) -> AssetBlob:
    """Decode a PNG/JPEG base64 data URI with strict validation."""

    try:
        header, encoded = value.split(",", 1)
    except ValueError as exc:
        raise MediaError("invalid image data URI") from exc
    if not header.lower().startswith("data:image/") or ";base64" not in header.lower():
        raise MediaError("image data URI must be base64 encoded")
    mime_type = header[5:].split(";", 1)[0].lower()
    if mime_type not in _ALLOWED_MIME:
        raise MediaError("only PNG and JPEG data URIs are supported")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MediaError("invalid base64 image data") from exc
    return validate_image_bytes(data, mime_type)


def load_local_image(path: Path) -> AssetBlob:
    """Load an application-selected local PNG/JPEG file."""

    file_path = Path(path)
    if not file_path.is_file():
        raise MediaError(f"image file does not exist: {file_path}")
    if file_path.stat().st_size > MAX_IMAGE_BYTES:
        raise MediaError(
            f"image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MiB limit"
        )
    return validate_image_bytes(file_path.read_bytes())


def _remote_host_allowed(hostname: str) -> bool:
    allowed = {
        item.strip().lower()
        for item in os.getenv("ODFORGE_REMOTE_IMAGE_HOSTS", "").split(",")
        if item.strip()
    }
    return hostname.lower() in allowed


def _assert_public_host(hostname: str) -> None:
    try:
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise MediaError(f"unable to resolve remote image host: {hostname}") from exc
    if not addresses:
        raise MediaError(f"remote image host has no addresses: {hostname}")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise MediaError("remote image host resolves to a non-public address")


def fetch_remote_image(url: str) -> AssetBlob:
    """Fetch an allowlisted public HTTPS image without following redirects."""

    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise MediaError("remote images require an HTTPS URL")
    if not _remote_host_allowed(parsed.hostname):
        raise MediaError(
            "remote image host is not in ODFORGE_REMOTE_IMAGE_HOSTS"
        )
    _assert_public_host(parsed.hostname)
    request = Request(
        url,
        headers={
            "Accept": "image/png,image/jpeg",
            "User-Agent": "ODForge/1.0",
        },
    )
    try:
        with build_opener(_NoRedirect).open(request, timeout=12) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_IMAGE_BYTES:
                raise MediaError("remote image exceeds size limit")
            data = response.read(MAX_IMAGE_BYTES + 1)
            content_type = response.headers.get("Content-Type", "")
    except MediaError:
        raise
    except Exception as exc:
        raise MediaError(f"remote image request failed: {exc}") from exc
    return validate_image_bytes(data, content_type)


class HttpImageProvider:
    """POST prompts to an app-owned endpoint that returns raw PNG/JPEG bytes."""

    def __init__(self, endpoint: str, api_key: str = "") -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise MediaError("image provider endpoint must be an HTTP(S) URL")
        self.endpoint = endpoint
        self.api_key = api_key

    def generate(self, prompt: str) -> AssetBlob:
        payload = json.dumps({"prompt": prompt}, ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "image/png,image/jpeg",
            "Content-Type": "application/json",
            "User-Agent": "ODForge/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            self.endpoint, data=payload, headers=headers, method="POST"
        )
        try:
            with build_opener(_NoRedirect).open(request, timeout=60) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_IMAGE_BYTES:
                    raise MediaError("generated image exceeds size limit")
                data = response.read(MAX_IMAGE_BYTES + 1)
                content_type = response.headers.get("Content-Type", "")
        except MediaError:
            raise
        except Exception as exc:
            raise MediaError(f"image provider request failed: {exc}") from exc
        return validate_image_bytes(data, content_type)


def configured_image_provider() -> ImageProvider | None:
    """Return the configured provider, or ``None`` when generation is off."""

    backend = os.getenv("ODFORGE_IMAGE_BACKEND", "off").strip().lower()
    if backend in {"", "off", "none"}:
        return None
    if backend != "http":
        raise MediaError(
            "ODFORGE_IMAGE_BACKEND must be 'off' or 'http'"
        )
    endpoint = os.getenv("ODFORGE_IMAGE_ENDPOINT", "").strip()
    if not endpoint:
        raise MediaError(
            "ODFORGE_IMAGE_ENDPOINT is required for the http image backend"
        )
    return HttpImageProvider(
        endpoint=endpoint,
        api_key=os.getenv("ODFORGE_IMAGE_API_KEY", ""),
    )


AssetInput = AssetBlob | bytes | Path


def normalize_assets(
    assets: Mapping[str, AssetInput] | None,
) -> dict[str, AssetBlob]:
    """Validate an app-owned ``id -> image`` mapping."""

    normalized: dict[str, AssetBlob] = {}
    for asset_id, value in (assets or {}).items():
        if not asset_id or not all(
            char.isalnum() or char in "_-" for char in asset_id
        ):
            raise MediaError(f"unsafe asset id: {asset_id!r}")
        if isinstance(value, AssetBlob):
            blob = value
        elif isinstance(value, bytes):
            blob = validate_image_bytes(value)
        else:
            blob = load_local_image(Path(value))
        normalized[asset_id] = blob
    return normalized


def resolve_image(
    image: ImageSpec,
    assets: Mapping[str, AssetBlob],
    provider: ImageProvider | None = None,
) -> AssetBlob | None:
    """Resolve an image spec. Missing/unavailable sources return ``None``.

    Invalid bytes still raise ``MediaError``; a missing asset or disabled
    provider becomes a visible placeholder in the renderer instead of a crash.
    """

    if image.src.startswith("asset://"):
        return assets.get(image.src.removeprefix("asset://"))
    if image.src.startswith("data:"):
        return decode_data_uri(image.src)
    if image.src.startswith("https://"):
        return fetch_remote_image(image.src)
    if image.prompt and provider is not None:
        return provider.generate(image.prompt)
    return None


__all__ = [
    "MAX_IMAGE_BYTES",
    "AssetBlob",
    "AssetInput",
    "HttpImageProvider",
    "ImageProvider",
    "MediaError",
    "configured_image_provider",
    "decode_data_uri",
    "fetch_remote_image",
    "load_local_image",
    "normalize_assets",
    "resolve_image",
    "validate_image_bytes",
]
