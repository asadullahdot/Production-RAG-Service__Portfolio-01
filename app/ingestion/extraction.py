"""Text extraction per page / section / slide (PLAN.md §3, §8.1 step 6).

PDF: one section per page. DOCX: sections split at Heading-style paragraphs, tables flattened as
``cell | cell``; a DOCX without headings is a single section (page 1). PPTX (B5): one section per slide.
TXT: one section. MD (B5): sections split at ``#`` headings.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass

from langchain_core.documents import Document

from app.errors import CorruptFile, EncryptedFile, FileTooLarge


def Section(page: int, text: str) -> Document:
    """One page / section / slide as a LangChain ``Document`` (``metadata["page"]`` is 1-based)."""
    return Document(page_content=text, metadata={"page": page})


@dataclass
class Extracted:
    sections: list[Document]

    @property
    def num_pages(self) -> int:
        return len(self.sections)

    @property
    def total_chars(self) -> int:
        return sum(len(s.page_content.strip()) for s in self.sections)


def extract(data: bytes, file_type: str, max_pages: int | None = None) -> Extracted:
    try:
        if file_type == "pdf":
            return _pdf(data, max_pages)
        if file_type == "docx":
            return _docx(data)
        if file_type == "pptx":
            return _pptx(data)
        if file_type == "txt":
            return Extracted([Section(1, _decode(data))])
        if file_type == "md":
            return _markdown(_decode(data))
    except (EncryptedFile, CorruptFile, FileTooLarge):
        raise
    except Exception as exc:  # parser libraries raise many unrelated types on bad input
        raise CorruptFile() from exc
    raise CorruptFile()


def _decode(data: bytes) -> str:
    return data.decode("utf-8-sig")


def _pdf(data: bytes, max_pages: int | None = None) -> Extracted:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        raise EncryptedFile()
    if max_pages is not None and len(reader.pages) > max_pages:  # reject before the costly text extraction
        raise FileTooLarge(f"The document has {len(reader.pages)} pages (max {max_pages}).")
    sections = [Section(i, (page.extract_text() or "")) for i, page in enumerate(reader.pages, start=1)]
    return Extracted(sections)


def _docx(data: bytes) -> Extracted:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(io.BytesIO(data))
    sections: list[list[str]] = [[]]
    saw_heading = False
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, document)
            style = (para.style.name if para.style is not None else "") or ""
            text = para.text.strip()
            if (style.startswith("Heading") or style == "Title") and text:
                if saw_heading or any(sections[-1]):
                    sections.append([])
                saw_heading = True
            if text:
                sections[-1].append(text)
        elif tag == "tbl":
            table = Table(child, document)
            for row in table.rows:
                cells = []
                for cell in row.cells:
                    value = cell.text.strip().replace("\n", " ")
                    if not cells or cells[-1] != value:  # merged cells repeat their text
                        cells.append(value)
                line = " | ".join(cells).strip(" |")
                if line:
                    sections[-1].append(line)
    out = [Section(i, "\n".join(parts)) for i, parts in enumerate(sections, start=1) if parts]
    return Extracted(out or [Section(1, "")])


def _pptx(data: bytes) -> Extracted:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    sections = []
    for i, slide in enumerate(prs.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                parts.append(shape.text_frame.text)
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    parts.append(" | ".join(c.text.strip() for c in row.cells))
        sections.append(Section(i, "\n".join(p for p in parts if p.strip())))
    return Extracted(sections)


_MD_HEADING = re.compile(r"^#{1,6}\s+\S")


def _markdown(text: str) -> Extracted:
    chunks: list[list[str]] = [[]]
    for line in text.splitlines():
        if _MD_HEADING.match(line) and any(chunks[-1]):
            chunks.append([])
        chunks[-1].append(line)
    out = [Section(i, "\n".join(c)) for i, c in enumerate(chunks, start=1) if any(x.strip() for x in c)]
    return Extracted(out or [Section(1, "")])
