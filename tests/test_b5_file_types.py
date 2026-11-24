"""B5: PPTX, TXT and Markdown uploads (PLAN.md §16). Page = slide number for PPTX, heading section for Markdown."""
from __future__ import annotations

import io
import zipfile

import pytest
from pptx import Presentation
from pptx.util import Inches

from app.config import Settings
from app.ingestion.extraction import extract
from tests.conftest import Env
from tests.make_files import make_pdf, make_pptx

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def post(client, data, name, mime):
    return client.post("/documents", files={"file": (name, data, mime)})


def deck(slides: list[tuple[str, str]], group_text: str | None = None) -> bytes:
    """Slides of (title, speaker notes); an optional text box inside a group shape on the first slide."""
    prs = Presentation()
    for i, (title, notes) in enumerate(slides):
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.title.text = title
        if notes:
            slide.notes_slide.notes_text_frame.text = notes
        if group_text and i == 0:
            group = slide.shapes.add_group_shape()
            box = group.shapes.add_textbox(Inches(1), Inches(3), Inches(4), Inches(1))
            box.text_frame.text = group_text
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ---- defaults and the health endpoint ---------------------------------------------------------------------
def test_all_five_types_are_allowed_by_default():
    assert set(Settings(_env_file=None).allowed_file_types.split(",")) == {"pdf", "docx", "pptx", "txt", "md"}


def test_health_tells_the_ui_which_types_to_offer(tmp_path):
    e = Env(tmp_path)
    assert e.client.get("/health").json()["file_types"] == ["docx", "md", "pdf", "pptx", "txt"]


def test_a_type_can_still_be_switched_off(tmp_path):
    e = Env(tmp_path, allowed_file_types="pdf,docx")
    r = post(e.client, deck([("Alpha", "")]), "d.pptx", PPTX_MIME)
    assert r.status_code == 415 and "docx, pdf" in r.json()["error"]["message"]
    assert post(e.client, b"hello", "n.txt", "text/plain").status_code == 415


# ---- PPTX ---------------------------------------------------------------------------------------------------
def test_pptx_page_is_the_slide_number_and_speaker_notes_are_searchable(tmp_path):
    e = Env(tmp_path)
    data = deck([("Quarterly overview", ""), ("Battery supply risk", "Second supplier Norlith is in qualification until January.")])
    r = post(e.client, data, "review.pptx", PPTX_MIME)
    assert r.status_code == 201 and r.json()["file_type"] == "pptx" and r.json()["num_pages"] == 2
    body = e.client.post("/ask", json={"question": "Which second supplier Norlith is in qualification until January?"}).json()
    top = body["retrieved_chunks"][0]
    assert top["file_name"] == "review.pptx" and top["page"] == 2          # the slide that carries the note
    assert "Speaker notes:" in top["text"] and "Norlith" in top["text"]


def test_pptx_extraction_reads_group_shapes_notes_and_tables():
    ex = extract(deck([("Title one", "Note one"), ("Title two", "")], group_text="Text inside a group"), "pptx")
    assert [s.metadata["page"] for s in ex.sections] == [1, 2]
    assert "Text inside a group" in ex.sections[0].page_content
    assert "Speaker notes: Note one" in ex.sections[0].page_content
    assert "Speaker notes" not in ex.sections[1].page_content            # a slide without notes gets no empty marker
    assert extract(make_pptx(["Only slide"]), "pptx").num_pages == 1


def test_pptx_with_macros_or_missing_parts_is_rejected(tmp_path):
    e = Env(tmp_path)
    macro = io.BytesIO()
    with zipfile.ZipFile(macro, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("ppt/presentation.xml", "<p:presentation/>")
        z.writestr("ppt/vbaProject.bin", b"macro")
    r = post(e.client, macro.getvalue(), "evil.pptx", PPTX_MIME)
    assert r.status_code == 400 and r.json()["error"]["code"] == "malicious_file"
    hollow = io.BytesIO()
    with zipfile.ZipFile(hollow, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
    assert post(e.client, hollow.getvalue(), "hollow.pptx", PPTX_MIME).status_code == 400
    assert post(e.client, b"not a zip", "fake.pptx", PPTX_MIME).status_code == 415
    assert post(e.client, deck([("A", "")]), "d.pptx", "application/pdf").status_code == 415  # MIME does not match


# ---- TXT ----------------------------------------------------------------------------------------------------
def test_txt_is_one_section_on_page_one_and_searchable(tmp_path):
    e = Env(tmp_path)
    text = ("The warehouse opens at six in the morning and closes at ten at night. " * 3).encode()
    r = post(e.client, text, "hours.txt", "text/plain")
    assert r.status_code == 201 and r.json()["file_type"] == "txt" and r.json()["num_pages"] == 1
    body = e.client.post("/ask", json={"question": "When does the warehouse open in the morning?"}).json()
    assert body["retrieved_chunks"][0]["page"] == 1 and body["retrieved_chunks"][0]["file_name"] == "hours.txt"


@pytest.mark.parametrize("data,why", [(b"caf\xe9 au lait is served with fresh bread every single morning here", "not UTF-8"),
                                      (b"text with a NUL byte \x00 inside it and more words after that", "binary")])
def test_txt_must_be_utf8_text(tmp_path, data, why):
    assert post(Env(tmp_path).client, data, "bad.txt", "text/plain").status_code == 415, why


def test_empty_and_whitespace_only_text_files_are_400(tmp_path):
    e = Env(tmp_path)
    assert post(e.client, b"", "empty.txt", "text/plain").status_code == 400
    assert post(e.client, b"   \n\n   ", "blank.txt", "text/plain").status_code == 400


def test_utf8_bom_is_accepted(tmp_path):
    data = b"\xef\xbb\xbf" + b"A plain text file that starts with a byte order mark and says refunds take thirty days."
    assert post(Env(tmp_path).client, data, "bom.txt", "text/plain").status_code == 201


# ---- Markdown -----------------------------------------------------------------------------------------------
MD = (b"# Introduction\nThis guide explains the product in plain words and says hello to every reader.\n\n"
      b"# Installation\nRun the installer and choose the default folder to finish the installation quickly.\n\n"
      b"## Troubleshooting\nIf the installer fails, restart the computer and run the installer again as administrator.\n")


def test_markdown_page_is_the_heading_section(tmp_path):
    e = Env(tmp_path)
    r = post(e.client, MD, "guide.md", "text/markdown")
    assert r.status_code == 201 and r.json()["file_type"] == "md" and r.json()["num_pages"] == 3
    body = e.client.post("/ask", json={"question": "If the installer fails, restart the computer and run again as administrator?"}).json()
    assert body["retrieved_chunks"][0]["page"] == 3                       # the Troubleshooting section


def test_markdown_extraction_splits_at_headings_only():
    ex = extract(MD, "md")
    assert [s.metadata["page"] for s in ex.sections] == [1, 2, 3]
    assert ex.sections[1].page_content.startswith("# Installation")
    assert extract(b"no headings here, just a paragraph of text", "md").num_pages == 1


@pytest.mark.parametrize("name,mime", [("guide.md", "text/markdown"), ("guide.markdown", "text/plain"), ("guide.md", "application/octet-stream"),
                                       ("guide.md", "text/x-markdown")])
def test_markdown_extensions_and_mime_variants_are_accepted(tmp_path, name, mime):
    r = post(Env(tmp_path).client, MD, name, mime)
    assert r.status_code == 201 and r.json()["file_type"] == "md"


def test_duplicate_and_delete_work_for_every_new_type(tmp_path):
    e = Env(tmp_path)
    for name, data, mime in (("a.txt", b"Notes about refunds that are accepted within thirty days of purchase.", "text/plain"),
                             ("b.md", MD, "text/markdown"), ("c.pptx", deck([("Refund policy overview slide", "Refunds are accepted within thirty days of purchase.")]), PPTX_MIME)):
        first = post(e.client, data, name, mime)
        assert first.status_code == 201, name
        assert post(e.client, data, name, mime).status_code == 409, name
        assert e.client.delete(f"/documents/{first.json()['doc_id']}").status_code == 204
        assert post(e.client, data, name, mime).status_code == 201, name  # re-upload after delete works
    assert e.client.post("/documents", files={"file": ("p.pdf", make_pdf(["A pdf document still works alongside the other file types and has enough text."]), "application/pdf")}).status_code == 201
