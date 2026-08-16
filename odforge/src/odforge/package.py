"""ODF package writer: zip packaging + auto-generated manifest.

Independent module (no dependency on ir/render). Packages hand-written
ODF part streams (content.xml/styles.xml/...) into a valid ODF zip that
obeys the mimetype-first-stored rule and carries a generated
META-INF/manifest.xml.
"""

from __future__ import annotations

import os
import time
import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ODT_MIMETYPE = "application/vnd.oasis.opendocument.text"
ODP_MIMETYPE = "application/vnd.oasis.opendocument.presentation"
ODS_MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"

_MANIFEST_NS = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"

# Media types inferred from the file extension of binary (bytes) parts.
_BINARY_MEDIA_TYPES = {
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _media_type(name: str, content: str | bytes) -> str:
    """Resolve the manifest media-type for a package part.

    str content is treated as XML (``text/xml``); bytes content has its
    media-type inferred from the file extension, falling back to the generic
    ``application/octet-stream`` for unknown binary extensions.
    """
    if isinstance(content, str):
        return "text/xml"
    suffix = Path(name).suffix.lower()
    return _BINARY_MEDIA_TYPES.get(suffix, "application/octet-stream")


def build_manifest(mimetype: str, parts: dict[str, str | bytes]) -> str:
    """Build a META-INF/manifest.xml document listing the package parts.

    Pure function: given the package mimetype and the parts mapping, returns a
    well-formed manifest XML string. The root "/" entry carries the document
    mimetype; str parts are declared as text/xml, while bytes parts get a
    media-type inferred from their file extension.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<manifest:manifest xmlns:manifest="{_MANIFEST_NS}" manifest:version="1.2">',
        ' <manifest:file-entry manifest:full-path="/" '
        f'manifest:media-type="{escape(mimetype, {chr(34): "&quot;"})}"/>',
    ]
    for name, content in parts.items():
        media_type = _media_type(name, content)
        lines.append(
            f' <manifest:file-entry manifest:full-path="{escape(name, {chr(34): "&quot;"})}"'
            f' manifest:media-type="{media_type}"/>'
        )
    lines.append("</manifest:manifest>")
    return "\n".join(lines)


def write_odf_package(path: Path, mimetype: str, parts: dict[str, str | bytes]) -> Path:
    """Write an ODF zip package to ``path`` and return that path.

    The ``mimetype`` entry is written first and uncompressed (ZIP_STORED),
    as required by the ODF spec. Remaining ``parts`` and the generated
    manifest are written with ZIP_DEFLATED. str parts are encoded as UTF-8;
    bytes parts (e.g. images) are written verbatim.

    The zip is assembled in a sibling temp file and moved into place with
    ``os.replace``: QA repair and per-slide 重生 re-render the same path a
    reader may be streaming at that moment (the web download endpoint), and a
    truncate-in-place ``ZipFile(path, "w")`` hands that reader a half-written
    archive with a 200. Windows refuses the replace while such a handle is
    open, so it is retried briefly — a corrupt download is the one outcome
    that must stay impossible.
    """
    path = Path(path)
    tmp_path = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.part")
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            mimetype_info = zipfile.ZipInfo("mimetype")
            mimetype_info.compress_type = zipfile.ZIP_STORED
            z.writestr(mimetype_info, mimetype.encode("utf-8"))

            for name, content in parts.items():
                data = content.encode("utf-8") if isinstance(content, str) else content
                z.writestr(name, data)

            manifest = build_manifest(mimetype, parts)
            z.writestr("META-INF/manifest.xml", manifest.encode("utf-8"))

        last_error: OSError | None = None
        for _ in range(5):
            try:
                os.replace(tmp_path, path)
                return path
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.2)
        raise last_error  # a reader held the file for >1s; keep the old copy intact
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
