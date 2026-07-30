"""Bounded reads for untrusted ZIP-based office documents.

ODF and OOXML files are ZIP archives.  Their central-directory size metadata
can be checked before decompression, then each member is read through a hard
cap as a second line of defence against forged metadata and decompression
bombs.
"""

from __future__ import annotations

import zipfile
from typing import Final

MAX_ARCHIVE_MEMBERS: Final = 10_000
MAX_TOTAL_UNCOMPRESSED: Final = 512 * 1024 * 1024
MAX_XML_MEMBER: Final = 16 * 1024 * 1024
MAX_MANIFEST_MEMBER: Final = 16 * 1024 * 1024
MAX_MIMETYPE_MEMBER: Final = 1024
MAX_XML_MARKUP_TOKENS: Final = 250_000


class ArchiveLimitError(ValueError):
    """Raised when a ZIP archive exceeds ODForge's resource limits."""


def inspect_archive(
    archive: zipfile.ZipFile,
    *,
    max_members: int = MAX_ARCHIVE_MEMBERS,
    max_total_uncompressed: int = MAX_TOTAL_UNCOMPRESSED,
) -> list[zipfile.ZipInfo]:
    """Return members after validating archive-wide resource limits."""
    infos = archive.infolist()
    if len(infos) > max_members:
        raise ArchiveLimitError(
            f"archive has {len(infos)} members; limit is {max_members}"
        )

    total = sum(info.file_size for info in infos)
    if total > max_total_uncompressed:
        raise ArchiveLimitError(
            "archive declares "
            f"{total} uncompressed bytes; limit is {max_total_uncompressed}"
        )
    return infos


def read_member(
    archive: zipfile.ZipFile,
    name: str,
    *,
    max_bytes: int,
) -> bytes:
    """Read one member without allowing more than ``max_bytes`` output."""
    info = archive.getinfo(name)
    if info.file_size > max_bytes:
        raise ArchiveLimitError(
            f"archive member {name!r} declares {info.file_size} bytes; "
            f"limit is {max_bytes}"
        )

    with archive.open(info) as source:
        data = source.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ArchiveLimitError(
            f"archive member {name!r} exceeds the {max_bytes}-byte read limit"
        )
    return data


def read_xml_member(
    archive: zipfile.ZipFile,
    name: str,
    *,
    max_bytes: int = MAX_XML_MEMBER,
    max_markup_tokens: int = MAX_XML_MARKUP_TOKENS,
) -> bytes:
    """Read XML while bounding both bytes and approximate tree complexity.

    Counting ``<`` is deliberately conservative: every XML element contributes
    at least one opening delimiter, so rejecting excessive delimiters prevents
    a compact document from expanding into millions of in-memory tree nodes.
    """
    data = read_member(archive, name, max_bytes=max_bytes)
    markup_tokens = data.count(b"<")
    if markup_tokens > max_markup_tokens:
        raise ArchiveLimitError(
            f"archive member {name!r} has {markup_tokens} markup tokens; "
            f"limit is {max_markup_tokens}"
        )
    return data
