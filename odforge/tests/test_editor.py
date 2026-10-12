"""Tests for the web editor's backend: ``scene`` (read) and ``odpedit`` (write).

Every edit is checked twice: in the scene the editor sees next, and in the file on
disk, which must stay a valid ODF package that LibreOffice opens.
"""

import shutil
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from odforge.editor_api import create_editor_app
from odforge.odpedit import Document, EditError
from odforge.render.odp import render_odp
from odforge.scene import build_scene, cm, scene_of
from odforge.validate import find_soffice, validate_odf

DEMO = Path(__file__).resolve().parents[1] / "demo" / "tree.odp"


@pytest.fixture
def deck(tmp_path) -> Path:
    target = tmp_path / "deck.odp"
    shutil.copyfile(DEMO, target)
    return target


def _page(state, n):
    return state["scene"]["pages"][n]


def _el(state, element_id):
    page, _ = element_id.split("/")
    return next(e for e in state["scene"]["pages"][int(page)]["elements"] if e["id"] == element_id)


def _texts(element):
    return ["".join(r["text"] for r in p["runs"]) for p in element["text"]["paragraphs"]]


# -- reading ---------------------------------------------------------------------


def test_lengths_convert_to_centimetres():
    assert cm("1in") == pytest.approx(2.54)
    assert cm("72pt") == pytest.approx(2.54)
    assert cm("5mm") == pytest.approx(0.5)
    assert cm("nonsense", 3) == 3


def test_scene_of_the_demo_deck():
    scene = scene_of(DEMO)
    assert (scene["width"], scene["height"]) == (28.0, 15.75)
    assert len(scene["pages"]) == 17
    kinds = {e["kind"] for p in scene["pages"] for e in p["elements"]}
    assert "unsupported" not in kinds
    assert {f["href"] for f in scene["fonts"]} >= {"Fonts/NotoSansTC-Regular.otf"}


def test_text_keeps_styles_line_breaks_and_bullets():
    scene = scene_of(DEMO)
    page = scene["pages"][7]
    title = next(e for e in page["elements"] if e["kind"] == "text" and "三種走訪順序" in str(e))
    heading = title["text"]["paragraphs"][1]["runs"][0]
    assert heading["size"] == 28 and heading["weight"] == 700
    note = next(e for e in page["elements"] if e["kind"] == "text" and "再走訪左右子樹" in str(e))
    assert "\n" in _texts(note)[0]
    listed = [
        p
        for pg in scene["pages"]
        for e in pg["elements"]
        if e.get("text")
        for p in e["text"]["paragraphs"]
        if "bullet" in p
    ]
    assert listed and listed[0]["bullet"]["char"]


def test_frames_without_padding_get_libreoffice_insets():
    scene = scene_of(DEMO)
    frame = next(e for e in scene["pages"][0]["elements"] if e["kind"] == "text")
    assert frame["text"]["padding"] == {"top": 0.125, "right": 0.25, "bottom": 0.125, "left": 0.25}


def test_master_page_number_follows_the_page():
    scene = scene_of(DEMO)
    numbers = [_texts(e)[0] for e in scene["pages"][7]["master"] if e["kind"] == "text"]
    assert numbers == ["8"]


def test_a_freshly_rendered_deck_reads_back(tmp_path, sample_presentation):
    out = render_odp(sample_presentation, tmp_path / "p.odp")
    scene = scene_of(out)
    assert len(scene["pages"]) == len(sample_presentation.slides)


# -- editing ---------------------------------------------------------------------


def test_move_and_resize(deck):
    doc = Document(deck)
    state = doc.apply([{"op": "geom", "id": "7/11", "x": 12, "y": 7, "w": 6, "h": 4.5}])
    assert {k: _el(state, "7/11")[k] for k in ("x", "y", "w", "h")} == {
        "x": 12,
        "y": 7,
        "w": 6,
        "h": 4.5,
    }
    assert _el(Document(deck).state(), "7/11")["x"] == 12  # on disk


def test_lines_move_by_their_ends(deck):
    doc = Document(deck)
    line = next(e for e in doc.state()["scene"]["pages"][7]["elements"] if e["kind"] == "line")
    state = doc.apply([{"op": "geom", "id": line["id"], "x1": 1, "y1": 2, "x2": 3, "y2": 2}])
    moved = _el(state, line["id"])
    assert (moved["x1"], moved["y1"], moved["x2"], moved["y2"]) == (1, 2, 3, 2)


def test_tiny_boxes_are_refused(deck):
    with pytest.raises(EditError, match="0.05"):
        Document(deck).apply([{"op": "geom", "id": "7/11", "w": 0.01}])


def test_text_edit_keeps_the_paragraph_style(deck):
    doc = Document(deck)
    title = next(
        e
        for e in doc.state()["scene"]["pages"][7]["elements"]
        if e["kind"] == "text" and "三種走訪順序" in str(e)
    )
    state = doc.apply([{"op": "text", "id": title["id"], "paragraphs": ["03 走訪", "新的標題"]}])
    edited = _el(state, title["id"])
    assert _texts(edited) == ["03 走訪", "新的標題"]
    assert edited["text"]["paragraphs"][1]["runs"][0]["size"] == 28


def test_growing_a_list_keeps_its_bullets(deck):
    doc = Document(deck)
    listed = next(
        e
        for e in doc.state()["scene"]["pages"][2]["elements"]
        if e.get("text") and any("bullet" in p for p in e["text"]["paragraphs"])
    )
    state = doc.apply([{"op": "text", "id": listed["id"], "paragraphs": list("ABCDEFG")}])
    paragraphs = _el(state, listed["id"])["text"]["paragraphs"]
    assert [p["runs"][0]["text"] for p in paragraphs] == list("ABCDEFG")
    assert all("bullet" in p for p in paragraphs)


def test_add_duplicate_order_delete(deck):
    doc = Document(deck)
    count = len(_page(doc.state(), 0)["elements"])
    state = doc.apply([{"op": "add_text", "page": 0, "x": 3, "y": 3, "text": "新的"}])
    (new_id,) = state["created"]
    assert _texts(_el(state, new_id)) == ["新的"]
    state = doc.apply([{"op": "duplicate", "id": new_id}])
    (copy_id,) = state["created"]
    assert _el(state, copy_id)["x"] == pytest.approx(3.5)
    state = doc.apply([{"op": "order", "id": copy_id, "to": "back"}])
    assert state["created"] == ["0/0"]
    state = doc.apply([{"op": "delete", "id": "0/0"}, {"op": "add_rect", "page": 0}])
    assert len(_page(state, 0)["elements"]) == count + 2


def test_page_operations(deck):
    doc = Document(deck)
    titles = lambda st: [p["name"] for p in st["scene"]["pages"]]  # noqa: E731
    before = titles(doc.state())
    state = doc.apply([{"op": "page_duplicate", "page": 0}])
    assert len(titles(state)) == 18 and state["created"] == ["page:1"]
    assert len(set(titles(state))) == 18  # page names stay unique
    state = doc.apply([{"op": "page_move", "page": 0, "to": 5}])
    assert titles(state)[5] == before[0]
    state = doc.apply([{"op": "page_delete", "page": 5}, {"op": "page_add", "after": 0}])
    assert len(titles(state)) == 18
    assert _page(state, 1)["elements"] == []


def test_a_failing_batch_changes_nothing(deck):
    doc = Document(deck)
    with pytest.raises(EditError):
        doc.apply([{"op": "geom", "id": "7/11", "x": 1}, {"op": "geom", "id": "99/0", "x": 1}])
    assert _el(doc.state(), "7/11")["x"] == pytest.approx(11.3)
    assert not doc.state()["canUndo"]


def test_undo_and_redo(deck):
    doc = Document(deck)
    doc.apply([{"op": "geom", "id": "7/11", "x": 12}])
    doc.apply([{"op": "geom", "id": "7/11", "x": 13}])
    assert _el(doc.undo(), "7/11")["x"] == 12
    assert _el(doc.undo(), "7/11")["x"] == pytest.approx(11.3)
    assert _el(doc.redo(), "7/11")["x"] == 12
    state = doc.apply([{"op": "geom", "id": "7/11", "x": 14}])
    assert not state["canRedo"]


def test_unknown_operations_are_refused(deck):
    with pytest.raises(EditError, match="不認得"):
        Document(deck).apply([{"op": "explode"}])


def test_not_a_presentation(tmp_path, sample_text_doc):
    from odforge.render.odt import render_odt

    with pytest.raises(EditError, match="不是簡報"):
        Document(render_odt(sample_text_doc, tmp_path / "t.odt"))


def test_edited_file_stays_a_valid_package(deck):
    doc = Document(deck)
    doc.apply(
        [
            {"op": "geom", "id": "7/11", "x": 12},
            {"op": "add_text", "page": 2, "text": "新的"},
            {"op": "page_duplicate", "page": 3},
        ]
    )
    with zipfile.ZipFile(deck) as package:
        first = package.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
    report = validate_odf(deck, with_soffice=find_soffice() is not None)
    assert report.ok, report.gates


def test_build_scene_survives_a_bare_document():
    from lxml import etree

    content = etree.fromstring(
        b'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        b'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0">'
        b"<office:body><office:presentation><draw:page><draw:custom-shape/></draw:page>"
        b"</office:presentation></office:body></office:document-content>"
    )
    scene = build_scene(content, etree.Element("styles"))
    assert scene["pages"][0]["elements"][0]["kind"] == "unsupported"


# -- HTTP --------------------------------------------------------------------------


def test_editor_api(deck):
    client = TestClient(create_editor_app(deck))
    state = client.get("/api/doc").json()
    assert state["name"] == "deck.odp"
    ok = client.post("/api/doc/ops", json={"ops": [{"op": "geom", "id": "7/11", "x": 12}]})
    assert ok.status_code == 200 and ok.json()["canUndo"]
    bad = client.post("/api/doc/ops", json={"ops": [{"op": "geom", "id": "99/0", "x": 1}]})
    assert bad.status_code == 422 and "第 100 頁" in bad.json()["detail"]
    assert client.post("/api/doc/undo").json()["canRedo"]
    font = client.get("/api/doc/file/Fonts/NotoSansTC-Regular.otf")
    assert font.status_code == 200 and font.headers["content-type"] == "font/otf"
    assert client.get("/api/doc/file/content.xml").status_code == 404
    assert client.get("/api/doc/file/Fonts/../content.xml").status_code == 404
    assert client.get("/api/doc/download").content[:2] == b"PK"


# -- rich text, clipboard, pictures ---------------------------------------------------


def test_rich_runs_become_spans_only_where_they_differ(deck):
    doc = Document(deck)
    state = doc.apply(
        [
            {
                "op": "text",
                "id": "7/2",
                "paragraphs": [
                    {
                        "align": "center",
                        "runs": [
                            {"text": "三者", "color": "#5B6470", "size": 16},
                            {"text": "完整", "weight": 700, "color": "#c62828", "size": 16},
                            {"text": "造訪\n節點", "italic": True, "underline": True, "size": 20},
                        ],
                    }
                ],
            }
        ]
    )
    paragraph = _el(state, "7/2")["text"]["paragraphs"][0]
    assert paragraph["align"] == "center"
    runs = paragraph["runs"]
    assert [r["text"] for r in runs] == ["三者", "完整", "造訪\n節點"]
    assert "weight" not in runs[0] and runs[1]["weight"] == 700 and runs[1]["color"] == "#C62828"
    assert runs[2]["italic"] and runs[2]["underline"] and runs[2]["size"] == 20
    with zipfile.ZipFile(deck) as package:
        content = package.read("content.xml").decode()
    assert content.count("<text:span") >= 2


def test_same_look_reuses_one_style(deck):
    doc = Document(deck)
    bold = {"runs": [{"text": "粗", "weight": 700}]}
    doc.apply(
        [
            {"op": "text", "id": "7/2", "paragraphs": [bold]},
            {"op": "text", "id": "7/7", "paragraphs": [bold]},
        ]
    )
    with zipfile.ZipFile(deck) as package:
        content = package.read("content.xml").decode()
    assert content.count('style:name="OEdT') == 1


def test_bad_alignment_is_refused(deck):
    with pytest.raises(EditError, match="對齊"):
        Document(deck).apply(
            [
                {
                    "op": "text",
                    "id": "7/2",
                    "paragraphs": [{"runs": [{"text": "x"}], "align": "diagonal"}],
                }
            ]
        )


def test_copy_to_another_page(deck):
    doc = Document(deck)
    items = doc.clip(["7/11", "7/12"])
    before = len(_page(doc.state(), 1)["elements"])
    state = doc.apply([{"op": "paste", "page": 1, "items": items, "dx": 0, "dy": 0}])
    assert len(_page(state, 1)["elements"]) == before + 2
    assert _el(state, state["created"][0])["x"] == pytest.approx(11.3)


def test_paste_refuses_foreign_xml(deck):
    doc = Document(deck)
    with pytest.raises(EditError):
        doc.apply([{"op": "paste", "page": 0, "items": ["<html/>"]}])
    with pytest.raises(EditError):
        doc.apply([{"op": "paste", "page": 0, "items": ["not xml"]}])


def test_insert_picture(deck):
    import base64

    png = DEMO.with_name("tree.png").read_bytes()
    doc = Document(deck)
    state = doc.apply(
        [
            {
                "op": "add_image",
                "page": 2,
                "x": 1,
                "y": 1,
                "w": 8,
                "h": 4.6,
                "data": base64.b64encode(png).decode(),
            }
        ]
    )
    image = _el(state, state["created"][0])
    assert image["kind"] == "image" and image["href"].startswith("Pictures/")
    assert doc.member(image["href"]) == png
    with zipfile.ZipFile(deck) as package:
        assert image["href"] in package.read("META-INF/manifest.xml").decode()
    report = validate_odf(deck, with_soffice=find_soffice() is not None)
    assert report.ok, report.gates


def test_picture_must_be_png_or_jpeg(deck):
    import base64

    with pytest.raises(EditError, match="PNG 或 JPEG"):
        Document(deck).apply(
            [{"op": "add_image", "page": 0, "data": base64.b64encode(b"GIF89a....").decode()}]
        )


def test_clip_endpoint(deck):
    client = TestClient(create_editor_app(deck))
    items = client.post("/api/doc/clip", json={"ids": ["7/11"]}).json()["items"]
    assert items and items[0].startswith("<draw:rect")
    assert client.post("/api/doc/clip", json={"ids": ["99/0"]}).status_code == 422


def test_package_files_are_served_inert(deck):
    with zipfile.ZipFile(deck, "a") as package:
        package.writestr("Pictures/evil.html", "<script>alert(1)</script>")
    client = TestClient(create_editor_app(deck))
    evil = client.get("/api/doc/file/Pictures/evil.html")
    assert evil.headers["content-type"] == "application/octet-stream"
    assert evil.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in evil.headers["content-security-policy"]


def test_family_change_declares_the_font(deck):
    doc = Document(deck)
    state = doc.apply(
        [
            {
                "op": "text",
                "id": "7/2",
                "paragraphs": [{"runs": [{"text": "換字型", "family": "Some Font"}]}],
            }
        ]
    )
    assert _el(state, "7/2")["text"]["paragraphs"][0]["runs"][0]["family"] == ["Some Font"]
    assert {"family": "Some Font", "embeddable": False} in state["fontChoices"]


def test_new_characters_are_added_to_embedded_fonts(deck):
    from odforge import docfonts
    from odforge.fontembed import find_font

    pytest.importorskip("fontTools")
    if find_font("Noto Sans TC", False) is None:
        pytest.skip("Noto Sans TC source font not installed")
    doc = Document(deck)
    before = docfonts.coverage(doc.member("Fonts/NotoSansTC-Regular.otf"))
    assert ord("鬱") not in before
    state = doc.apply([{"op": "text", "id": "7/2", "paragraphs": ["鬱金香"]}])
    after = docfonts.coverage(doc.member("Fonts/NotoSansTC-Regular.otf"))
    assert ord("鬱") in after
    assert all(f["rev"] for f in state["scene"]["fonts"])


# -- several writers -------------------------------------------------------------------


def test_outside_changes_are_picked_up(deck):
    editor = Document(deck)
    editor.apply([{"op": "geom", "id": "7/11", "x": 12}])
    version = editor.poll()
    Document(deck).apply([{"op": "text", "id": "7/2", "paragraphs": ["別人改的"]}])
    assert editor.poll() == version + 1
    state = editor.state()
    assert _texts(_el(state, "7/2")) == ["別人改的"]
    assert _el(state, "7/11")["x"] == 12  # the earlier edit is still in the file
    assert not state["canUndo"]  # history does not reach across another writer


def test_edits_apply_on_top_of_outside_changes(deck):
    editor = Document(deck)
    Document(deck).apply([{"op": "text", "id": "7/2", "paragraphs": ["別人改的"]}])
    state = editor.apply([{"op": "geom", "id": "7/11", "x": 13}])
    assert _texts(_el(state, "7/2")) == ["別人改的"]


def test_version_endpoint(deck):
    client = TestClient(create_editor_app(deck))
    first = client.get("/api/doc/version").json()["version"]
    Document(deck).apply([{"op": "geom", "id": "7/11", "x": 12}])
    assert client.get("/api/doc/version").json()["version"] == first + 1


def test_mcp_outline_and_edit(deck):
    from odforge.mcp_server import edit_odp, outline_odp

    outline = outline_odp(str(deck))
    assert "第 8 頁(page=7)" in outline and "[7/2] text" in outline
    result = edit_odp(
        str(deck), [{"op": "add_text", "page": 0, "x": 2, "y": 2, "text": "agent 加的"}]
    )
    assert result.startswith("ok:") and "created: 0/" in result and "agent 加的" in result
    assert edit_odp(str(deck), [{"op": "geom", "id": "99/0", "x": 1}]).startswith("error:")
    assert edit_odp(str(deck), []).startswith("error:")
    assert outline_odp(str(deck) + ".missing").startswith("error: file not found")


# -- comments ----------------------------------------------------------------------------


def test_comments_follow_their_element_and_survive_edits(deck):
    doc = Document(deck)
    state = doc.apply(
        [
            {"op": "comment_add", "id": "7/2", "text": "這句太長"},
            {"op": "comment_add", "page": 7, "x": 20, "y": 12, "text": "整頁太擠"},
        ]
    )
    assert state["created"] == ["c7/0", "c7/1"]
    comments = _page(state, 7)["comments"]
    assert [(c["text"], c["target"]) for c in comments] == [("這句太長", "7/2"), ("整頁太擠", None)]
    # Elements added later do not shift the comment off its element.
    state = doc.apply(
        [{"op": "add_text", "page": 7, "text": "新"}, {"op": "geom", "id": "7/2", "x": 3}]
    )
    assert _page(state, 7)["comments"][0]["target"] == "7/2"
    assert len(_page(state, 7)["elements"]) == 26  # comments are not drawn elements
    state = doc.apply([{"op": "comment_delete", "comment": "c7/0"}])
    assert [c["text"] for c in _page(state, 7)["comments"]] == ["整頁太擠"]
    report = validate_odf(deck, with_soffice=find_soffice() is not None)
    assert report.ok, report.gates


def test_comment_text_does_not_trigger_font_embedding(deck):
    from odforge import docfonts
    from odforge.scene import read_package

    doc = Document(deck)
    doc.apply([{"op": "comment_add", "id": "7/2", "text": "鬱鬱蔥蔥"}])
    assert "鬱" not in docfonts.document_text(list(read_package(deck)))


def test_comment_errors(deck):
    doc = Document(deck)
    with pytest.raises(EditError, match="空的"):
        doc.apply([{"op": "comment_add", "id": "7/2", "text": "  "}])
    with pytest.raises(EditError, match="找不到留言"):
        doc.apply([{"op": "comment_delete", "comment": "c7/5"}])


def test_agent_sees_and_resolves_comments(deck):
    from odforge.mcp_server import edit_odp, outline_odp

    Document(deck).apply([{"op": "comment_add", "id": "7/2", "text": "改短"}])
    outline = outline_odp(str(deck))
    assert "待處理留言 1 則" in outline and "留言 [c7/0] 關於 [7/2]:改短" in outline
    result = edit_odp(
        str(deck),
        [
            {"op": "text", "id": "7/2", "paragraphs": ["短"]},
            {"op": "comment_delete", "comment": "c7/0"},
        ],
    )
    assert result.startswith("ok:") and "待處理留言" not in result


# -- the web feedback loop ----------------------------------------------------------------


def test_submit_hands_drafts_to_the_ai(deck):
    doc = Document(deck)
    doc.apply([{"op": "comment_add", "id": "7/2", "text": "改短"}])
    state = doc.apply([{"op": "comments_submit"}])
    assert state["created"] == ["c7/0"]
    assert _page(state, 7)["comments"][0]["state"] == "submitted"
    with pytest.raises(EditError, match="沒有要交給 AI"):
        doc.apply([{"op": "comments_submit"}])


def test_wait_for_feedback_returns_submitted_comments_then_done(deck):
    import asyncio

    from odforge.mcp_server import wait_for_feedback

    doc = Document(deck)
    doc.apply([{"op": "comment_add", "id": "7/2", "text": "改短"}])
    assert asyncio.run(wait_for_feedback(str(deck), 1)).startswith("waiting:")
    doc.apply([{"op": "comments_submit"}])
    got = asyncio.run(wait_for_feedback(str(deck), 1))
    assert got.startswith("feedback:") and "[c7/0]" in got and "關於 [7/2]" in got
    doc.apply([{"op": "comment_delete", "comment": "c7/0"}, {"op": "session", "state": "done"}])
    assert asyncio.run(wait_for_feedback(str(deck), 1)).startswith("done:")
    assert Document(deck).state()["session"] == "open"  # picked up, so cleared


def test_export_serves_the_current_checked_file(deck):
    client = TestClient(create_editor_app(deck))
    client.post(
        "/api/doc/ops", json={"ops": [{"op": "text", "id": "7/2", "paragraphs": ["匯出前改的"]}]}
    )
    response = client.get("/api/doc/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    import io

    with zipfile.ZipFile(io.BytesIO(response.content)) as package:
        assert "匯出前改的" in package.read("content.xml").decode()


def test_export_refuses_a_broken_file(deck, monkeypatch):
    from odforge import validate

    client = TestClient(create_editor_app(deck))

    from types import SimpleNamespace

    bad = SimpleNamespace(ok=False, gates={"xml": (False, "content.xml 壞了")})
    monkeypatch.setattr(validate, "validate_odf", lambda *_a, **_k: bad)
    response = client.get("/api/doc/export")
    assert response.status_code == 500 and "content.xml 壞了" in response.json()["detail"]


# -- groups and ready-made parts ------------------------------------------------------------


def test_group_move_scale_edit_and_ungroup(deck):
    doc = Document(deck)
    state = doc.apply([{"op": "group", "ids": ["7/11", "7/13", "7/14", "7/15"]}])
    (gid,) = state["created"]
    group = _el(state, gid)
    assert group["kind"] == "group" and [c["id"] for c in group["children"]] == [
        f"{gid}.{k}" for k in range(4)
    ]
    state = doc.apply(
        [
            {
                "op": "geom",
                "id": gid,
                "x": group["x"] + 1,
                "y": group["y"],
                "w": group["w"] * 2,
                "h": group["h"],
            }
        ]
    )
    moved = _el(state, gid)
    assert moved["x"] == pytest.approx(group["x"] + 1) and moved["w"] == pytest.approx(
        group["w"] * 2
    )
    state = doc.apply([{"op": "text", "id": f"{gid}.1", "paragraphs": ["群組內改字"]}])
    assert _texts(_el(state, gid)["children"][1]) == ["群組內改字"]
    state = doc.apply([{"op": "ungroup", "id": gid}])
    assert len(state["created"]) == 4
    report = validate_odf(deck, with_soffice=find_soffice() is not None)
    assert report.ok, report.gates


def test_group_needs_two_top_level_elements(deck):
    doc = Document(deck)
    with pytest.raises(EditError, match="兩個"):
        doc.apply([{"op": "group", "ids": ["7/11"]}])
    with pytest.raises(EditError, match="同一頁"):
        doc.apply([{"op": "group", "ids": ["7/11", "6/1"]}])


def test_deleting_the_last_member_removes_the_group(deck):
    doc = Document(deck)
    (gid,) = doc.apply([{"op": "group", "ids": ["7/11", "7/12"]}])["created"]
    before = len(_page(doc.state(), 7)["elements"])
    state = doc.apply([{"op": "delete", "id": f"{gid}.1"}, {"op": "delete", "id": f"{gid}.0"}])
    assert len(_page(state, 7)["elements"]) == before - 1


def test_theme_comes_from_the_deck(deck):
    from odforge.components import infer_theme
    from odforge.scene import read_package

    theme = infer_theme(*read_package(deck))
    assert theme.accent == "#1A4B8C" and theme.ink == "#1F2733"
    assert theme.display_font == "Noto Serif TC" and theme.body_font == "Noto Sans TC"
    assert theme.card_style  # the deck's own card look is reused


@pytest.mark.parametrize(
    "kind",
    [
        "heading",
        "body",
        "kicker",
        "card",
        "step",
        "metric",
        "quote",
        "takeaway",
        "timeline",
        "divider",
        "rect",
        "ellipse",
    ],
)
def test_every_part_inserts(deck, kind):
    doc = Document(deck)
    state = doc.apply([{"op": "add_component", "page": 1, "kind": kind, "x": 3, "y": 3}])
    el = _el(state, state["created"][0])
    assert el["kind"] in ("group", "text", "rect", "ellipse", "line")


def test_parts_validate_and_use_the_deck_style(deck):
    doc = Document(deck)
    state = doc.apply([{"op": "add_component", "page": 1, "kind": "card", "x": 3, "y": 3}])
    card = _el(state, state["created"][0])
    title = card["children"][2]["text"]["paragraphs"][0]["runs"][0]
    assert title["family"] == ["Noto Serif TC"] and title["weight"] == 700
    assert card["children"][1]["text"]["paragraphs"][0]["runs"][0]["color"] == "#1A4B8C"
    with pytest.raises(EditError, match="不認得的元素"):
        doc.apply([{"op": "add_component", "page": 1, "kind": "rocket"}])
    report = validate_odf(deck, with_soffice=find_soffice() is not None)
    assert report.ok, report.gates
