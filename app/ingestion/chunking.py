"""Chunking with ``RecursiveCharacterTextSplitter.split_documents`` (PLAN.md §3). Splits per page; never across pages."""
from __future__ import annotations

from dataclasses import dataclass

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


@dataclass
class ChunkDraft:
    chunk_idx: int
    page: int
    text: str


def chunk_sections(sections: list[Document], chunk_size: int, chunk_overlap: int) -> list[ChunkDraft]:
    """Split each page/section on its own, so a chunk keeps the page number of the page it came from."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    pages = [d for d in sections if d.page_content.strip()]
    drafts: list[ChunkDraft] = []
    for piece in splitter.split_documents(pages):  # copies each page's metadata onto its chunks
        text = piece.page_content.strip()
        if text:
            drafts.append(ChunkDraft(chunk_idx=len(drafts), page=int(piece.metadata["page"]), text=text))
    return drafts
