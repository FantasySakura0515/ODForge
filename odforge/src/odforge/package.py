"""ODF package writer: zip packaging + auto-generated manifest.

Independent module (no dependency on ir/render). Packages hand-written
ODF part streams (content.xml/styles.xml/...) into a valid ODF zip that
obeys the mimetype-first-stored rule and carries a generated
META-INF/manifest.xml.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape

ODT_MIMETYPE = "application/vnd.oasis.opendocument.text"
ODP_MIMETYPE = "application/vnd.oasis.opendocument.presentation"
ODS_MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"

_MANIFEST_NS = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"


def build_manifest(mimetype: str, part_names: Iterable[str]) -> str:
    """Build a META-INF/manifest.xml document listing the package parts.

    Pure function: given the package mimetype and the part names, returns a
    well-formed manifest XML string. The root "/" entry carries the document
    mimetype; every other part is declared as text/xml.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<manifest:manifest xmlns:manifest="{_MANIFEST_NS}" manifest:version="1.2">',
        f' <manifest:file-entry manifest:full-path="/" manifest:media-type="{escape(mimetype, {chr(34): "&quot;"})}"/>',
    ]
    for part in part_names:
        lines.append(
            f' <manifest:file-entry manifest:full-path="{escape(part, {chr(34): "&quot;"})}" manifest:media-type="text/xml"/>'
        )
    lines.append("</manifest:manifest>")
    return "\n".join(lines)


def write_odf_package(path: Path, mimetype: str, parts: dict[str, str]) -> Path:
    """Write an ODF zip package to ``path`` and return that path.

    The ``mimetype`` entry is written first and uncompressed (ZIP_STORED),
    as required by the ODF spec. Remaining ``parts`` and the generated
    manifest are written with ZIP_DEFLATED. All content is encoded as UTF-8.
    """
    path = Path(path)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        mimetype_info = zipfile.ZipInfo("mimetype")
        mimetype_info.compress_type = zipfile.ZIP_STORED
        z.writestr(mimetype_info, mimetype.encode("utf-8"))

        for name, content in parts.items():
            z.writestr(name, content.encode("utf-8"))

        manifest = build_manifest(mimetype, parts.keys())
        z.writestr("META-INF/manifest.xml", manifest.encode("utf-8"))

    return path
