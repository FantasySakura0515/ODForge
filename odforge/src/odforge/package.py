"""ODF package writer: zip packaging + auto-generated manifest.

Independent module (no dependency on ir/render). Packages hand-written
ODF part streams (content.xml/styles.xml/...) into a valid ODF zip that
obeys the mimetype-first-stored rule and carries a generated
META-INF/manifest.xml.
"""

from __future__ import annotations

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
        f' <manifest:file-entry manifest:full-path="/" manifest:media-type="{escape(mimetype, {chr(34): "&quot;"})}"/>',
    ]
    for name, content in parts.items():
        media_type = _media_type(name, content)
        lines.append(
            f' <manifest:file-entry manifest:full-path="{escape(name, {chr(34): "&quot;"})}" manifest:media-type="{media_type}"/>'
        )
    lines.append("</manifest:manifest>")
    return "\n".join(lines)


def write_odf_package(path: Path, mimetype: str, parts: dict[str, str | bytes]) -> Path:
    """Write an ODF zip package to ``path`` and return that path.

    The ``mimetype`` entry is written first and uncompressed (ZIP_STORED),
    as required by the ODF spec. Remaining ``parts`` and the generated
    manifest are written with ZIP_DEFLATED. str parts are encoded as UTF-8;
    bytes parts (e.g. images) are written verbatim.
    """
    path = Path(path)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        mimetype_info = zipfile.ZipInfo("mimetype")
        mimetype_info.compress_type = zipfile.ZIP_STORED
        z.writestr(mimetype_info, mimetype.encode("utf-8"))

        for name, content in parts.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            z.writestr(name, data)

        manifest = build_manifest(mimetype, parts)
        z.writestr("META-INF/manifest.xml", manifest.encode("utf-8"))

    return path
