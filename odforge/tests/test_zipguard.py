from __future__ import annotations

import zipfile

import pytest

from odforge.zipguard import (
    ArchiveLimitError,
    inspect_archive,
    read_member,
    read_xml_member,
)


def test_inspect_archive_rejects_too_many_members(tmp_path):
    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.xml", "<a/>")
        zf.writestr("b.xml", "<b/>")

    with zipfile.ZipFile(archive) as zf:
        with pytest.raises(ArchiveLimitError, match="members"):
            inspect_archive(zf, max_members=1)


def test_inspect_archive_rejects_excessive_total_uncompressed_size(tmp_path):
    archive = tmp_path / "large.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.xml", "x" * 128)

    with zipfile.ZipFile(archive) as zf:
        with pytest.raises(ArchiveLimitError, match="uncompressed"):
            inspect_archive(zf, max_total_uncompressed=64)


def test_read_member_rejects_oversized_member_before_decompression(tmp_path):
    archive = tmp_path / "member.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.xml", "x" * 128)

    with zipfile.ZipFile(archive) as zf:
        with pytest.raises(ArchiveLimitError, match="content.xml"):
            read_member(zf, "content.xml", max_bytes=64)


def test_read_xml_member_rejects_excessive_markup_before_parsing(tmp_path):
    archive = tmp_path / "nodes.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.xml", "<root>" + ("<a/>" * 16) + "</root>")

    with zipfile.ZipFile(archive) as zf:
        with pytest.raises(ArchiveLimitError, match="markup tokens"):
            read_xml_member(
                zf,
                "content.xml",
                max_bytes=1024,
                max_markup_tokens=10,
            )
